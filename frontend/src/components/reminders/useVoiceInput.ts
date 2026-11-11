"use client";

/**
 * 语音输入。两条路，一条是流式（边说边出字），一条是一句话识别（说完才有字）。
 *
 * ## 为什么两条都留着
 *
 * 流式要腾讯云的 APPID，一句话识别只要 SecretId/SecretKey。少配一个东西就
 * 让整个语音功能消失，是最差的设计；所以流式是**增强**，它挂掉（没配 APPID、
 * 握手失败、浏览器不支持 WS）就悄悄退回一句话识别 —— 用户感觉到的差别只是
 * "字不是边说边出来"，功能本身还在。
 *
 * 降级发生在**会话中途**也能用：录制期间一直在累积 PCM（60 秒约 3.8MB），
 * 所以 WebSocket 建不起来时，同一段音频可以直接走一次性上传，用户不用重说。
 *
 * ## 为什么不用 MediaRecorder
 *
 * MediaRecorder 在 Chrome 上输出 `audio/webm;codecs=opus`，腾讯云不认这个容器
 * （一句话识别要 wav/pcm/mp3/m4a/aac；流式要 pcm/opus/speex…）。
 * 两条路都要 PCM，所以统一用 Web Audio 采，不碰 MediaRecorder。
 *
 * ## 流式的音频格式是**裸 PCM**
 *
 * `voice_format=1` 意味着每包都是纯 16bit 采样，没有 WAV 头。WAV 头只对
 * "整段文件"有意义，流式下每 200ms 一包，塞头反而要上游按容器去解析。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

const TARGET_RATE = 16000;
const MAX_SECONDS = 60;
/** 每包 200ms。16k × 2 字节 × 0.2s = 6400 字节，正是腾讯云建议的节奏 */
const CHUNK_BYTES = 6400;
const CHUNK_SAMPLES = CHUNK_BYTES / 2;
/** 说完了等上游吐完尾包的上限。超过就用手上已有的文本收尾 */
const FINISH_TIMEOUT_MS = 8000;

export type VoicePhase = "idle" | "recording" | "finishing" | "transcribing";

/** 后端 `api/capture.py` 定义的帧，一一对应。前端不碰腾讯云的 `slice_type`。 */
export type StreamFrame =
  | { type: "ready" }
  | { type: "partial"; text: string }
  | { type: "segment"; text: string }
  | { type: "done"; text: string }
  | { type: "error"; message: string; retryable?: boolean };

type Callbacks = {
  /** 一次性模式的整段结果 */
  onText: (text: string) => void;
  onError: (message: string) => void;
  /** 流式模式的每一帧。只有 `streaming=true` 时才会被调用 */
  onFrame?: (frame: StreamFrame) => void;
  /** 走不走流式，由 `/api/capture/capabilities` 的 `stream` 决定 */
  streaming?: boolean;
};

type Session = {
  ctx: AudioContext;
  stream: MediaStream;
  source: MediaStreamAudioSourceNode;
  proc: ScriptProcessorNode;
  /** 一次性模式要整段；流式也留着，用于 WS 失败时就地降级 */
  chunks: Float32Array[];
  rate: number;
  timer: number;
  ws: WebSocket | null;
  wsOpen: boolean;
  /** 攒包用：凑满 CHUNK_SAMPLES 就发一包 */
  acc: Int16Array;
  accLen: number;
  resampler: StreamResampler | null;
  finishTimer: number;
};

/**
 * 峰值归一化：把整段录音的最大振幅拉到 `target`，最多放大 `maxGain` 倍。
 *
 * ## 为什么需要
 *
 * 识别引擎吃的是 16bit PCM。手机麦克风在正常握持距离下峰值常常只有 0.05-0.15，
 * 也就是只用掉了 16 位里的 12 位左右 —— 声母（s/sh/x/j/q/zh/ch）的能量本来就只有
 * 韵母的几分之一，再被量化掉一截，转写就会成片地"听成别的字"。
 * 把峰值顶到 0.85 等于免费多出 3-4 bit 的有效精度。
 *
 * ## 为什么只有一次性模式用
 *
 * 归一化要"知道整段的最大值"，只有录完才算得出来。流式下的对应物是采集时的
 * AGC（`autoGainControl: true`）—— 那是同一件事的实时版本，已经开了。
 * 在流式里再叠一层自适应增益就是双重放大，字间会忽大忽小，反而更难认。
 *
 * ## 为什么有下限
 *
 * 峰值低于 `MIN_PEAK` 说明大概率是"点了麦克风没说话"，此时放大只是把底噪
 * 喂给识别引擎，反而更容易凭空识别出一句话。宁可原样上传，让它返回空。
 */
const MIN_PEAK = 0.02;

export function normalizePeak(
  samples: Float32Array,
  target = 0.85,
  maxGain = 8
): Float32Array {
  let peak = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const v = samples[i] < 0 ? -samples[i] : samples[i];
    if (v > peak) peak = v;
  }
  if (peak < MIN_PEAK) return samples;

  const gain = Math.min(target / peak, maxGain);
  // 已经够响就不动：放大一个本来就正常的录音只会让齿音更刺耳
  if (gain <= 1.02) return samples;

  const out = new Float32Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) out[i] = samples[i] * gain;
  return out;
}

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

/** Float32 [-1,1] → Int16 采样。流式用，没有容器头。 */
function toInt16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

/** 重采样到 16k。用 OfflineAudioContext 而不是手写线性插值：
 *  浏览器自带的抗混叠比手写的好，而且这是浏览器原生能力，不增加体积。
 *  **只用于一次性模式** —— 流式不能等整段录完。 */
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

/**
 * 实时重采样。只在 **浏览器不认 16k 采样率** 时才用（Safari 会忽略这个参数）。
 *
 * ## 为什么这个必须存在
 *
 * 流式直接发 PCM，没有"事后重采样"这一步。如果拿到的是 48k 的帧却当成 16k
 * 发出去，腾讯云按 16k 解会得到 3 倍速的音频 —— 人耳听是叽里呱啦，识别结果
 * 是一两个字或者全空。这个错**不会报错**，只会"识别不出来"。
 *
 * ## 为什么每帧独立插值、不跨帧保持相位
 *
 * 跨帧保相位要维护尾巴和小数偏移，代码复杂度翻几倍；而不保的代价是每帧
 * 接缝处最多 1/3 个输入样本的相位跳变（48k 下约 7 微秒），远小于一个音素的
 * 长度，对识别没有任何影响。用 `acc` 累积小数余量，保证长期采样率比例准确。
 */
class StreamResampler {
  private ratio: number;
  private acc = 0;

  constructor(from: number, to: number) {
    this.ratio = from / to;
  }

  push(frame: Float32Array): Float32Array {
    const want = frame.length / this.ratio + this.acc;
    const n = Math.floor(want);
    this.acc = want - n;
    const out = new Float32Array(n);
    for (let i = 0; i < n; i += 1) {
      const src = i * this.ratio;
      const j = Math.floor(src);
      const frac = src - j;
      const a = frame[j] ?? 0;
      const b = frame[j + 1] ?? a;
      out[i] = a * (1 - frac) + b * frac;
    }
    return out;
  }
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

/**
 * 流式端点的地址。
 *
 * ## 为什么不能写相对路径
 *
 * 生产环境 `/api/*` 全走 Next.js 的 rewrites 到后端 8000，但 **rewrites
 * 不支持 WebSocket 升级**（Next 官方限制），所以 nginx 必须把
 * `/api/capture/ws` 单独 proxy_pass 到 8000 并带上 Upgrade 头。
 * 浏览器这边看到的仍然是同域同路径，所以生产就是相对路径。
 *
 * ## 开发环境为什么是 8000
 *
 * 本机前端在 3100、后端在 8000，Next dev 同样代理不了 WS。
 * 这里按端口判断而不是按环境变量：手写一个 `NEXT_PUBLIC_ASR_WS` 意味着
 * 每个新拉代码的人都要先知道要去配它，而没配的症状是"语音静默失效"。
 */
export function streamUrl(): string {
  const override = process.env.NEXT_PUBLIC_ASR_WS;
  if (override) return override;
  if (typeof window === "undefined") return "";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const dev = process.env.NODE_ENV === "development";
  if (dev && window.location.port !== "8000") {
    return `ws://${window.location.hostname}:8000/api/capture/ws`;
  }
  return `${proto}//${window.location.host}/api/capture/ws`;
}

export function useVoiceInput({ onText, onError, onFrame, streaming = false }: Callbacks) {
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [seconds, setSeconds] = useState(0);
  /**
   * 实时音量 0-1，给波形用。
   *
   * 只做"能看出在说话"这一件事：手机收音的绝对电平差异很大（同一个字，
   * 离 10cm 和 30cm 能差 4 倍），所以这里用的是**相对当前录音**的 RMS 放大，
   * 不是音量表。用户看它只判断"麦克风有没有收到声音"，不做别的。
   */
  const [level, setLevel] = useState(0);
  const session = useRef<Session | null>(null);
  // 回调存 ref：调用方通常写成内联函数，直接进依赖数组会让 start/stop 每帧重建，
  // 而 stop 被重建时正在录音的 session 还是老的 —— 表现为"点了停止没反应"。
  const cbs = useRef({ onText, onError, onFrame });
  cbs.current = { onText, onError, onFrame };
  /** 实际走的哪条路。调用方据此决定界面画"边说边出字"还是"转写中" */
  const [activeStream, setActiveStream] = useState(false);

  /** 只关采集资源，**不关 WebSocket** —— 流式模式下还要靠它收尾包。 */
  const stopAudio = useCallback((cur: Session) => {
    window.clearInterval(cur.timer);
    cur.proc.onaudioprocess = null;
    try {
      cur.source.disconnect();
      cur.proc.disconnect();
    } catch {
      // 已经断开的节点再 disconnect 会抛，这不影响后续
    }
    cur.stream.getTracks().forEach((t) => t.stop());
    void cur.ctx.close();
    setLevel(0); // 不清的话，波形停在最后一帧的高度上，看起来像"还在录"
  }, []);

  const teardown = useCallback(() => {
    const cur = session.current;
    if (!cur) return null;
    session.current = null;
    window.clearTimeout(cur.finishTimer);
    stopAudio(cur);
    if (cur.ws) {
      // onclose/onmessage 之后要能区分"我主动关的"和"对面掉了"，
      // 先把回调摘掉再关，避免自己触发自己的错误提示
      cur.ws.onmessage = null;
      cur.ws.onclose = null;
      cur.ws.onerror = null;
      try {
        cur.ws.close();
      } catch {
        // 连接可能已经没了
      }
    }
    return cur;
  }, [stopAudio]);

  /** 流式：把写满了的包发出去。不满的留着，下次接着凑。 */
  const flush = useCallback((cur: Session, force = false) => {
    if (!cur.ws || !cur.wsOpen) return;
    if (cur.accLen === 0) return;
    // 不 force 时只发整包：上游按 200ms 节奏吃数据，发半包会让 VAD 判不准
    if (!force && cur.accLen < CHUNK_SAMPLES) return;
    const payload = cur.acc.slice(0, cur.accLen);
    try {
      cur.ws.send(payload.buffer);
    } catch {
      // 连接已断。不再重连：这一轮用户已经在说，重连会丢中间的字，
      // 收尾时走一次性上传更完整。
      cur.wsOpen = false;
    }
    cur.accLen = 0;
  }, []);

  const stop = useCallback(async () => {
    const cur = session.current;
    if (!cur) return;

    if (cur.ws && cur.wsOpen) {
      // ---------- 流式收尾 ----------
      stopAudio(cur);
      // 剩下的半包也要发：最后 100ms 常常正是最后一个字
      flush(cur, true);
      setSeconds(0);
      setPhase("finishing");

      let settled = false;
      const finish = (text: string | null) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(cur.finishTimer);
        teardown();
        setPhase("idle");
        if (text) cbs.current.onText(text);
      };

      // 上游没吐完（网络卡住）也要收尾，不能把用户晾在"正在整理"
      cur.finishTimer = window.setTimeout(() => {
        finish(null);
        cbs.current.onError("没听到结尾，再说一遍");
      }, FINISH_TIMEOUT_MS);

      try {
        cur.ws.send(JSON.stringify({ type: "end" }));
      } catch {
        finish(null);
        cbs.current.onError("网络断了，再说一遍");
      }
      return;
    }

    // ---------- 一次性收尾（含流式失败后的降级）----------
    const audio = teardown();
    if (!audio) return;
    setSeconds(0);

    const pcm = joined(audio.chunks);
    // 0.3 秒以下基本是误触（或点了立刻点停），直接当"没说话"
    if (pcm.length < audio.rate * 0.3) {
      setPhase("idle");
      cbs.current.onError("没录到声音，再说一遍");
      return;
    }

    setPhase("transcribing");
    try {
      // 先归一化再重采样：重采样是线性运算，顺序不影响结果，
      // 但先做能在原始采样率上判断"这段到底有没有人说话"（重采样后峰值会变）。
      const wav = encodeWav(await resample(normalizePeak(pcm), audio.rate), TARGET_RATE);
      const { text } = await api.capture.transcribe(wav);
      cbs.current.onText(text);
    } catch (err) {
      cbs.current.onError((err as Error)?.message || "语音没转成，先打字吧");
    } finally {
      setPhase("idle");
    }
  }, [stopAudio, teardown]);

  const start = useCallback(async () => {
    if (session.current || typeof navigator === "undefined") return;
    if (!navigator.mediaDevices?.getUserMedia) {
      cbs.current.onError("这个浏览器不支持录音，先打字吧");
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        // ★ 采集约束直接决定识别率，改之前先读完这段：
        //
        // noiseSuppression: **必须关**。它是为通话设计的降噪（WebRTC APM 那套），
        //   在手机上会优先削掉 2-4kHz 的弱能量段，而中文声母 s/sh/x/j/q/zh/ch
        //   正好在这个频段。开着它录出来的声音"人耳听着干净"，识别引擎却少了
        //   区分字头的信息 —— 表现为成片同音错字，且录音回放听起来完全正常。
        // echoCancellation: **关**。浏览器端 AEC 会对近场信号做非线性处理，
        //   同样伤辅音；我们也没有远端回声要消。
        // autoGainControl: **开**。补上麦克风增益。流式模式下它是唯一的电平
        //   保证（归一化要整段录完才能算，流式等不了）。
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: true,
        },
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

    // 直接按 16k 建 context：省掉一次 48k→16k 的重采样，少一处理论上的失真。
    // Safari 对自定义采样率支持不全 —— 那种情况下 `ctx.sampleRate` 会是 48000/44100，
    // 下面按它建 StreamResampler，否则发出去的 PCM 会被上游当成 3 倍速。
    let ctx: AudioContext;
    try {
      ctx = new AudioContext({ sampleRate: TARGET_RATE });
    } catch {
      ctx = new AudioContext();
    }
    // iOS 上新建的 context 可能是 suspended（尤其从静音开关/后台切回来），
    // 不 resume 的话 ScriptProcessor 不会回调，录完是一段全零 —— 表现为"没录到声音"。
    if (ctx.state === "suspended") {
      try {
        await ctx.resume();
      } catch {
        // 恢复不了也让流程继续：录不到会走"没录到声音"的分支，比卡在"录音中"强
      }
    }

    const cur: Session = {
      ctx,
      stream,
      source: ctx.createMediaStreamSource(stream),
      proc: null as unknown as ScriptProcessorNode,
      chunks: [],
      rate: ctx.sampleRate,
      timer: 0,
      ws: null,
      wsOpen: false,
      acc: new Int16Array(CHUNK_SAMPLES),
      accLen: 0,
      resampler: ctx.sampleRate === TARGET_RATE ? null : new StreamResampler(ctx.sampleRate, TARGET_RATE),
      finishTimer: 0,
    };

    // 1024 而不是 4096：4096 在 16k 下是 256ms 一帧，波形只有 4 帧/秒，看着像卡住。
    // 1024 是 64ms（约 15 帧/秒），够顺，又不至于让回调占满主线程。
    cur.proc = ctx.createScriptProcessor(1024, 1, 1);
    let lastLevelAt = 0;
    cur.proc.onaudioprocess = (event) => {
      // 必须拷一份：inputBuffer 的底层数组会被下一帧复用，
      // 直接 push 引用的话，录完只剩最后一帧的声音（听起来像"只有半个字"）
      const frame = event.inputBuffer.getChannelData(0);
      const owned = new Float32Array(frame);
      // 留着给降级用：WS 半路挂掉时，这段音频还能走一次性上传
      cur.chunks.push(owned);

      if (cur.wsOpen) {
        const pcm = toInt16(cur.resampler ? cur.resampler.push(owned) : owned);
        let at = 0;
        while (at < pcm.length) {
          const take = Math.min(CHUNK_SAMPLES - cur.accLen, pcm.length - at);
          cur.acc.set(pcm.subarray(at, at + take), cur.accLen);
          cur.accLen += take;
          at += take;
          if (cur.accLen >= CHUNK_SAMPLES) flush(cur);
        }
      }

      // 波形电平：70ms 节流。每帧 setState 会让整个抽屉每秒重渲染十几次，
      // 比波形本身更耗电。
      const now = Date.now();
      if (now - lastLevelAt >= 70) {
        lastLevelAt = now;
        let sum = 0;
        for (let i = 0; i < frame.length; i += 1) sum += frame[i] * frame[i];
        // ×6 是经验值：正常说话 RMS 在 0.02-0.08，乘完落在 0.12-0.5 ——
        // 不会一直贴着 0，也不会一开口就顶满。
        setLevel(Math.min(1, Math.sqrt(sum / frame.length) * 6));
      }
    };
    cur.source.connect(cur.proc);
    // 连到 destination 才触发 onaudioprocess。直连会把这路声音放出来（回声），
    // 所以中间挂一个 0 增益的节点。
    const mute = ctx.createGain();
    mute.gain.value = 0;
    cur.proc.connect(mute);
    mute.connect(ctx.destination);

    cur.timer = window.setInterval(() => {
      setSeconds((prev) => prev + 1);
    }, 1000);

    session.current = cur;
    setSeconds(0);
    setPhase("recording");

    // ---------- 流式连接放在采集起来之后 ----------
    // 反过来（先连再开麦）会让"连不上"卡在权限弹窗之前，用户看到的是一段静默
    // 的空白期，而那时候他还不知道要不要允许麦克风。
    if (!streaming) {
      setActiveStream(false);
      return;
    }
    setActiveStream(true);

    let ws: WebSocket;
    try {
      ws = new WebSocket(streamUrl());
    } catch {
      setActiveStream(false);
      return; // 建不起来就静默降级，收尾时走一次性上传
    }
    ws.binaryType = "arraybuffer";
    cur.ws = ws;

    ws.onopen = () => {
      // 真正能发音频要等 `ready`（那是上游握手成功的信号）。这里不能直接置
      // wsOpen=true：签名不对时连接也会 onopen，然后立刻被上游关掉。
    };
    ws.onmessage = (event) => {
      let frame: StreamFrame;
      try {
        frame = JSON.parse(String(event.data)) as StreamFrame;
      } catch {
        return;
      }
      if (frame.type === "ready") {
        cur.wsOpen = true;
        cbs.current.onFrame?.(frame);
        return;
      }
      cbs.current.onFrame?.(frame);
      if (frame.type === "done") {
        // 上游说全结束了：收尾（这里可能是自动结束的那条路，也可能是 stop 等的那条）
        window.clearTimeout(cur.finishTimer);
        const text = frame.text;
        teardown();
        setPhase("idle");
        if (text) cbs.current.onText(text);
        return;
      }
      if (frame.type === "error") {
        window.clearTimeout(cur.finishTimer);
        const wasOpen = cur.wsOpen;
        cur.wsOpen = false;
        cbs.current.onError(frame.message);
        if (!wasOpen) {
          // 握手就没成功 —— 立刻退回一次性识别，让用户不用重说
          setActiveStream(false);
          teardown();
          setPhase("idle");
        }
      }
    };
    ws.onclose = () => {
      if (cur.wsOpen) {
        cur.wsOpen = false;
        cbs.current.onError("语音连接断了，再说一遍");
        teardown();
        setPhase("idle");
      }
    };
    ws.onerror = () => {
      // onerror 之后必然有 onclose，错误提示交给 onclose 统一出，避免弹两次
    };
  }, [flush, streaming, teardown]);

  const cancel = useCallback(() => {
    teardown();
    setSeconds(0);
    setPhase("idle");
  }, [teardown]);

  // 卸载时一定要停：不停的话麦克风指示灯会一直亮着，用户以为被偷听
  useEffect(() => () => void teardown(), [teardown]);

  // 60 秒是上游单次上限，到点自动停 —— 不自动停的话上游会在 60 秒处
  // 强制断句，后面的字全丢，用户白说了一分钟。
  // 放在 effect 里而不是 setInterval 的更新函数里：更新函数在 StrictMode 下
  // 会被调用两次，等于 stop 跑两遍。
  useEffect(() => {
    if (phase === "recording" && seconds >= MAX_SECONDS) void stop();
  }, [phase, seconds, stop]);

  return { phase, seconds, level, start, stop, cancel, streaming: activeStream };
}
