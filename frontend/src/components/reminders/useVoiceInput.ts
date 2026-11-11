"use client";

/**
 * 语音输入：录音 → 16k 单声道 WAV → 上传转写 → 把文字交给调用方。
 *
 * ## 为什么不用 MediaRecorder
 *
 * MediaRecorder 在 Chrome 上输出 `audio/webm;codecs=opus`，**腾讯云一句话识别
 * 不认这个容器**（它收 wav / pcm / mp3 / m4a / aac / ogg-opus）。要么后端装
 * ffmpeg 转码（线上 2 核 1.7G，不值），要么在前端采 PCM 自己封 WAV —— 选后者。
 *
 * 另一个好处是采样率可控：识别引擎是 16k_zh，直接给 16k PCM 比让它去猜靠谱。
 * 手机麦克风普遍 48k，所以录完要重采样一次（OfflineAudioContext 做，不手写插值）。
 *
 * ## 状态只有三个
 *
 * idle / recording / transcribing。失败不是第四个状态 —— 失败之后还是 idle，
 * 只是调用方会收到一句人话。把失败做成状态的话，界面要再多一个"错误页"，
 * 而用户真正的下一步永远是「再说一遍」或「改成打字」，输入框一直在那儿。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

const TARGET_RATE = 16000;
const MAX_SECONDS = 60;

export type VoicePhase = "idle" | "recording" | "transcribing";

type Callbacks = {
  onText: (text: string) => void;
  onError: (message: string) => void;
};

type Session = {
  ctx: AudioContext;
  stream: MediaStream;
  source: MediaStreamAudioSourceNode;
  proc: ScriptProcessorNode;
  chunks: Float32Array[];
  rate: number;
  timer: number;
};

/** Float32 [-1,1] → 16bit PCM 的 WAV。手写 44 字节头，不引第三方库。 */
export function encodeWav(samples: Float32Array, rate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };

  ascii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true); // PCM 头长度
  view.setUint16(20, 1, true); // 1 = PCM
  view.setUint16(22, 1, true); // 单声道
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); // 每秒字节数
  view.setUint16(32, 2, true); // 每帧字节数
  view.setUint16(34, 16, true); // 位深
  ascii(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1, offset += 2) {
    // 先夹到 [-1,1]：不夹的话爆音会被截成随机值（负数溢出听起来像"咔"）
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

/** 重采样到 16k。用 OfflineAudioContext 而不是手写线性插值：
 *  浏览器自带的抗混叠比手写的好，而且这是浏览器原生能力，不增加体积。 */
async function resample(samples: Float32Array, from: number): Promise<Float32Array> {
  if (from === TARGET_RATE) return samples;
  const frames = Math.max(1, Math.round((samples.length * TARGET_RATE) / from));
  const ctx = new OfflineAudioContext(1, frames, TARGET_RATE);
  const buffer = ctx.createBuffer(1, samples.length, from);
  // 拷贝一份再塞进 AudioBuffer：`getChannelData` 拿到的是 Float32Array<ArrayBufferLike>，
  // 直接传给 copyToChannel 在 TS 5.7 之后过不了（可 SharedArrayBuffer 的泛型）
  const channel = new Float32Array(samples.length);
  channel.set(samples);
  buffer.copyToChannel(channel, 0);
  const node = ctx.createBufferSource();
  node.buffer = buffer;
  node.connect(ctx.destination);
  node.start();
  const rendered = await ctx.startRendering();
  return rendered.getChannelData(0);
}

function joined(chunks: Float32Array[]): Float32Array {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const out = new Float32Array(total);
  let at = 0;
  for (const c of chunks) {
    out.set(c, at);
    at += c.length;
  }
  return out;
}

export function useVoiceInput({ onText, onError }: Callbacks) {
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [seconds, setSeconds] = useState(0);
  const session = useRef<Session | null>(null);
  // 回调存 ref：调用方通常写成内联函数，直接进依赖数组会让 start/stop 每帧重建，
  // 而 stop 被重建时正在录音的 session 还是老的 —— 表现为"点了停止没反应"。
  const cbs = useRef({ onText, onError });
  cbs.current = { onText, onError };

  const teardown = useCallback(() => {
    const cur = session.current;
    if (!cur) return null;
    session.current = null;
    window.clearInterval(cur.timer);
    cur.proc.onaudioprocess = null;
    cur.source.disconnect();
    cur.proc.disconnect();
    cur.stream.getTracks().forEach((t) => t.stop());
    void cur.ctx.close();
    return cur;
  }, []);

  const stop = useCallback(async () => {
    const cur = teardown();
    if (!cur) return;
    setSeconds(0);

    const pcm = joined(cur.chunks);
    // 0.3 秒以下基本是误触（或点了立刻点停），直接当"没说话"
    if (pcm.length < cur.rate * 0.3) {
      setPhase("idle");
      cbs.current.onError("没录到声音，再说一遍");
      return;
    }

    setPhase("transcribing");
    try {
      const wav = encodeWav(await resample(pcm, cur.rate), TARGET_RATE);
      const { text } = await api.capture.transcribe(wav);
      cbs.current.onText(text);
    } catch (err) {
      cbs.current.onError((err as Error)?.message || "语音没转成，先打字吧");
    } finally {
      setPhase("idle");
    }
  }, [teardown]);

  const start = useCallback(async () => {
    if (session.current || typeof navigator === "undefined") return;
    if (!navigator.mediaDevices?.getUserMedia) {
      cbs.current.onError("这个浏览器不支持录音，先打字吧");
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch (err) {
      // 权限拒绝和"没有麦克风"在浏览器里是同一个 catch，但用户要做的事不同
      const name = (err as DOMException)?.name;
      cbs.current.onError(
        name === "NotAllowedError"
          ? "浏览器没给麦克风权限，在地址栏允许后再试"
          : "没找到麦克风，先打字吧"
      );
      return;
    }

    const ctx = new AudioContext();
    const source = ctx.createMediaStreamSource(stream);
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    const chunks: Float32Array[] = [];
    proc.onaudioprocess = (event) => {
      // 必须拷一份：inputBuffer 的底层数组会被下一帧复用，
      // 直接 push 引用的话，录完只剩最后一帧的声音（听起来像"只有半个字"）
      chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    };
    source.connect(proc);
    // 连到 destination 才触发 onaudioprocess。直连会把这路声音放出来（回声），
    // 所以中间挂一个 0 增益的节点。
    const mute = ctx.createGain();
    mute.gain.value = 0;
    proc.connect(mute);
    mute.connect(ctx.destination);

    const timer = window.setInterval(() => {
      setSeconds((prev) => prev + 1);
    }, 1000);

    session.current = { ctx, stream, source, proc, chunks, rate: ctx.sampleRate, timer };
    setSeconds(0);
    setPhase("recording");
  }, [stop]);

  const cancel = useCallback(() => {
    teardown();
    setSeconds(0);
    setPhase("idle");
  }, [teardown]);

  // 卸载时一定要停：不停的话麦克风指示灯会一直亮着，用户以为被偷听
  useEffect(() => () => void teardown(), [teardown]);

  // 60 秒是腾讯云单次上限，到点自动停 —— 不自动停的话上传会被 413 拒，
  // 用户白说了一分钟。放在 effect 里而不是 setInterval 的更新函数里：
  // 更新函数在 StrictMode 下会被调用两次，等于 stop 跑两遍。
  useEffect(() => {
    if (phase === "recording" && seconds >= MAX_SECONDS) void stop();
  }, [phase, seconds, stop]);

  return { phase, seconds, start, stop, cancel };
}
