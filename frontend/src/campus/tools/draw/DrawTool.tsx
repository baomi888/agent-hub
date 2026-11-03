"use client";

/**
 * 群抽签（仿微信）。
 *
 * 名单**不落盘**：这是临时工具，抽完就完事，存下来反而会让人下次打开看到上次的名单发呆。
 * 旧版也没有存，这里保持一致。
 */

import { useState } from "react";
import { drawOutcome, parseNames, shuffle } from "../../logic/draw";
import type { DrawOutcome } from "../../logic/draw";

const DEFAULT_NEED = 3;
const DEFAULT_SIM = 30;

export default function DrawTool() {
  const [title, setTitle] = useState("");
  const [need, setNeed] = useState(DEFAULT_NEED);
  const [listText, setListText] = useState("");
  const [simCount, setSimCount] = useState(DEFAULT_SIM);
  const [result, setResult] = useState<DrawOutcome | null>(null);
  const [single, setSingle] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  const names = parseNames(listText);

  function fillSimulated() {
    const count = Math.min(200, Math.max(1, Math.floor(simCount) || 1));
    const generated = Array.from({ length: count }, (_, i) => `第${i + 1}号`);
    setListText(generated.join("\n"));
    setMessage("");
  }

  function draw() {
    if (names.length === 0) {
      setMessage("先贴入参与者名单，或点「填入名单」生成编号。");
      return;
    }

    const wanted = Math.min(names.length, Math.max(1, Math.floor(need) || 1));
    setResult(drawOutcome(names, wanted));
    setSingle(null);
    setMessage("");
  }

  function drawOne() {
    if (names.length === 0) {
      setMessage("先贴入参与者名单，或点「填入名单」生成编号。");
      return;
    }

    const [pick] = shuffle(names);
    setSingle(pick ?? null);
    setResult(null);
    setMessage("");
  }

  return (
    <div id="campus-panel-draw" role="tabpanel" aria-label="群抽签">
      <div className="campus-card">
        <div className="campus-card-head">
          <h2 className="campus-card-title">开个奖</h2>
          <span className="campus-card-note" style={{ margin: 0 }}>
            名单 {names.length} 人
          </span>
        </div>

        <p className="campus-card-note" style={{ marginTop: 0 }}>
          写清事由和名额，把参与者名单贴进来（每行一个，模拟群友接龙），点「开奖」随机抽出中签者。
        </p>

        <div className="campus-field" style={{ marginBottom: 12 }}>
          <span>抽签事由</span>
          <input
            className="campus-input"
            type="text"
            value={title}
            placeholder="如：今晚谁请喝奶茶"
            onChange={(event) => setTitle(event.target.value)}
          />
        </div>

        <div className="campus-add-row" style={{ marginBottom: 12 }}>
          <label className="campus-field" style={{ maxWidth: 120 }}>
            <span>名额（人）</span>
            <input
              className="campus-input"
              type="number"
              min={1}
              max={999}
              value={String(need)}
              onChange={(event) => setNeed(Number.parseInt(event.target.value, 10) || DEFAULT_NEED)}
            />
          </label>

          <label className="campus-field" style={{ maxWidth: 130 }}>
            <span>模拟人数</span>
            <input
              className="campus-input"
              type="number"
              min={1}
              max={200}
              value={String(simCount)}
              onChange={(event) =>
                setSimCount(Number.parseInt(event.target.value, 10) || DEFAULT_SIM)
              }
            />
          </label>

          <button type="button" className="campus-btn" onClick={fillSimulated}>
            填入名单
          </button>
        </div>

        <label className="campus-field">
          <span>参与者名单</span>
          <textarea
            className="campus-textarea"
            value={listText}
            rows={8}
            placeholder={"每行一个参与者（姓名 / 学号）。留空可点「填入名单」生成编号。"}
            onChange={(event) => {
              setListText(event.target.value);
              setMessage("");
            }}
          />
        </label>

        <div className="campus-add-row" style={{ marginTop: 12 }}>
          <button type="button" className="campus-btn is-primary" onClick={draw}>
            开奖
          </button>
          <button type="button" className="campus-btn" onClick={drawOne}>
            随机点名一个
          </button>
        </div>

        {message !== "" && (
          <p className="campus-card-note" role="status" style={{ color: "var(--red-ink)" }}>
            {message}
          </p>
        )}
      </div>

      {(result !== null || single !== null) && (
        <div className="campus-card" aria-live="polite">
          {single !== null && (
            <>
              <div className="campus-card-head">
                <h2 className="campus-card-title">随机点名</h2>
              </div>
              <div className="campus-draw-col">
                <div className="campus-draw-head is-ok">抽中</div>
                <span className="campus-draw-name is-win">★ {single}</span>
              </div>
            </>
          )}

          {result !== null && (
            <>
              <div className="campus-card-head">
                <h2 className="campus-card-title">开奖结果</h2>
                <span className="campus-card-note" style={{ margin: 0 }}>
                  共 {result.winners.length + result.rest.length} 人参与，抽 {result.winners.length} 名
                  {title.trim() === "" ? "" : ` · ${title.trim()}`}
                </span>
              </div>

              <div className="campus-draw-col">
                <div className="campus-draw-head is-ok">中签（{result.winners.length}）</div>
                {result.winners.map((name) => (
                  <span key={name} className="campus-draw-name is-win">
                    ★ {name}
                  </span>
                ))}
              </div>

              {result.rest.length > 0 && (
                <div className="campus-draw-col">
                  <div className="campus-draw-head">未中（{result.rest.length}）</div>
                  {result.rest.map((name) => (
                    <span key={name} className="campus-draw-name">
                      {name}
                    </span>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
