# -*- coding: utf-8 -*-
"""
苞米agent 线上轻量压力测试
=========================
设计原则：**低并发、可熔断、不留脏数据**。
目标机器是阿里云 ECS 2 核 2G，不做破坏性压测，只测「真实用户路径的响应能力」。

测试项：
  A. 只读接口并发（全部经前端 :3000 代理链路 —— 后端 :8000 已只监听 127.0.0.1，
     公网直连不通，A 项里的「后端直连」那一档相应改成打前端）
  B. 文档上传链路（并发上传，验证 kb_lock 串行保护与流式写盘）
  C. 真实问答链路（SSE，测首字节 TTFB 与整轮耗时）

跑法：
  python stress_test.py
产出：stress_result.json + 控制台摘要
"""

import asyncio
import json
import statistics
import time

import aiohttp

FE = "http://8.163.62.25:3000"   # 前端（用户真实入口，Next 反代到后端 :8000）
# 后端 :8000 已改为只监听 127.0.0.1（无鉴权，不对外暴露），公网直连测不到。
# 要测后端裸性能，请在服务器上跑： curl -s http://127.0.0.1:8000/health
TIMEOUT = 30                     # 单次请求硬超时，超过即记失败，不重试


def pct(values, p):
    """手动算百分位，避免依赖 numpy。"""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def summary(name, rows):
    """rows: [(latency, status, size), ...]"""
    lat = [r[0] for r in rows]
    ok = [r for r in rows if r[1] == 200]
    fails = len(rows) - len(ok)
    out = {
        "项": name,
        "样本数": len(rows),
        "成功": len(ok),
        "失败": fails,
        "失败率": round(fails / len(rows) * 100, 1) if rows else 0,
        "P50_ms": round(statistics.median(lat) * 1000, 1) if lat else 0,
        "P95_ms": round(pct(lat, 95) * 1000, 1) if lat else 0,
        "最大_ms": round(max(lat) * 1000, 1) if lat else 0,
    }
    print(f"  {name:<34} 成功 {len(ok):>3}/{len(rows):<3} "
          f"失败率 {out['失败率']:>5.1f}%  "
          f"P50 {out['P50_ms']:>8.1f}ms  P95 {out['P95_ms']:>8.1f}ms  max {out['最大_ms']:>8.1f}ms")
    return out


async def timed_get(sess, url):
    t0 = time.perf_counter()
    try:
        async with sess.get(url, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
            body = await r.read()
            return (time.perf_counter() - t0, r.status, len(body))
    except Exception:
        return (time.perf_counter() - t0, -1, 0)


async def bench_get(sess, url, conc, n):
    """用 semaphore 控制并发度，总共发 n 次请求。"""
    sem = asyncio.Semaphore(conc)

    async def one():
        async with sem:
            return await timed_get(sess, url)

    return await asyncio.gather(*[one() for _ in range(n)])


async def main():
    result = {"测试时间": time.strftime("%Y-%m-%d %H:%M:%S"), "样例": []}
    print("=" * 100)
    print(f"苞米agent 线上轻量压力测试   {result['测试时间']}")
    print("目标: 阿里云 ECS 2核2G  |  链路: 本机(沙箱) -> 公网 -> 8.163.62.25")
    print("说明: 延迟含公网往返 RTT，不代表服务器纯处理耗时")
    print("=" * 100)

    async with aiohttp.ClientSession() as sess:
        # ---------- 先确认服务活着 ----------
        print("\n[0] 健康检查")
        # 后端 :8000 只监听回环，公网测不到 —— 探前端代理链路代替
        h = await timed_get(sess, f"{FE}/api/kb/")
        print(f"  /api/kb/ -> HTTP {h[1]}  {h[0]*1000:.0f}ms")
        if h[1] != 200:
            print("  服务不可达，终止测试")
            return

        # ---------- 拿一个真实会话和知识库 ----------
        sid, kb_id = None, None
        try:
            async with sess.get(f"{FE}/api/sessions",
                                timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                data = await r.json()
                lst = data if isinstance(data, list) else data.get("sessions", [])
                if lst:
                    sid = lst[0].get("sid") or lst[0].get("id")
        except Exception as e:
            print(f"  (取会话失败: {e})")
        try:
            async with sess.get(f"{FE}/api/kb/",
                                timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                data = await r.json()
                lst = data if isinstance(data, list) else data.get("kbs", [])
                if lst:
                    kb_id = lst[0].get("kb_id") or lst[0].get("id")
        except Exception as e:
            print(f"  (取知识库失败: {e})")
        print(f"  会话 sid = {sid}   知识库 kb_id = {kb_id}")

        # ---------- A. 只读接口并发 ----------
        print("\n[A] 只读接口并发（每档 20 次请求）")
        result["只读接口"] = []
        for label, url in [
            ("经前端代理 /api/kb/ (:3000)", f"{FE}/api/kb/"),
            ("经前端代理 /api/sessions (:3000)", f"{FE}/api/sessions"),
        ]:
            for conc in (2, 4, 8):
                rows = await bench_get(sess, url, conc, 20)
                result["只读接口"].append(
                    dict(summary(f"{label}  并发{conc}", rows), 并发=conc, 接口=label)
                )

        # ---------- B. 上传链路 ----------
        print("\n[B] 文档上传链路（6KB TXT，含切片+向量化+落盘）")
        result["上传"] = []
        tmp_kb = None
        doc = ("本项目采用 FastAPI 构建后端服务，使用 Chroma 作为向量数据库，"
               "前端基于 Next.js 与 React。知识库文档上传后会自动切片、"
               "调用百炼 text-embedding-v3 生成向量并写入 Chroma 集合。"
               "检索阶段先取候选池，再做余弦离群过滤与 MMR 多样性选择。\n") * 60
        payload = doc.encode("utf-8")
        try:
            async with sess.post(f"{FE}/api/kb/create", json={"name": "压测临时库"},
                                 timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                d = await r.json()
                tmp_kb = d.get("kb_id")
            print(f"  建立临时知识库: {tmp_kb}")
        except Exception as e:
            print(f"  建库失败，跳过上传测试: {e}")

        if tmp_kb:
            for conc in (1, 2, 3):
                sem = asyncio.Semaphore(conc)
                rows = []

                async def up(i):
                    form = aiohttp.FormData()
                    form.add_field("files", payload, filename=f"stress_{i}.txt",
                                   content_type="text/plain")
                    form.add_field("chunk_size", "500")
                    form.add_field("chunk_overlap", "50")
                    form.add_field("mode", "append")
                    t0 = time.perf_counter()
                    try:
                        async with sem, sess.post(
                            f"{FE}/api/kb/{tmp_kb}/upload", data=form,
                            timeout=aiohttp.ClientTimeout(total=60)
                        ) as r:
                            await r.read()
                            return (time.perf_counter() - t0, r.status, 0)
                    except Exception:
                        return (time.perf_counter() - t0, -1, 0)

                rows = await asyncio.gather(*[up(i) for i in range(conc * 2)])
                result["上传"].append(
                    dict(summary(f"上传 6KB TXT  并发{conc}", rows), 并发=conc)
                )

            # 清理，绝不留脏数据
            try:
                async with sess.delete(f"{FE}/api/kb/{tmp_kb}/",
                                       timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
                    print(f"  清理临时知识库 -> HTTP {r.status}")
            except Exception as e:
                print(f"  清理失败(需手工删除 {tmp_kb}): {e}")

        # ---------- C. 真实问答链路 ----------
        print("\n[C] 真实问答链路（SSE 流式，mode=rag，含检索+大模型生成）")
        result["问答"] = []
        if sid:
            for conc in (1, 2, 3):
                sem = asyncio.Semaphore(conc)
                rows = []

                async def ask(i):
                    body = {"sid": sid,
                            "question": "简单介绍一下这个项目用到了哪些技术？",
                            "top_k": 3, "mode": "rag"}
                    t0 = time.perf_counter()
                    ttfb = None
                    try:
                        async with sem, sess.post(
                            f"{FE}/api/chat/stream", json=body,
                            timeout=aiohttp.ClientTimeout(total=120)
                        ) as r:
                            async for chunk in r.content.iter_any():
                                if ttfb is None and chunk:
                                    ttfb = time.perf_counter() - t0
                            return (time.perf_counter() - t0, r.status,
                                    (ttfb or 0) * 1000)
                    except Exception:
                        return (time.perf_counter() - t0, -1, 0)

                rows = await asyncio.gather(*[ask(i) for i in range(conc)])
                lat = [r[0] for r in rows]
                ok = [r for r in rows if r[1] == 200]
                ttfbs = [r[2] for r in rows if r[2] > 0]
                item = {
                    "项": f"SSE 问答  并发{conc}", "并发": conc,
                    "样本数": len(rows), "成功": len(ok),
                    "失败": len(rows) - len(ok),
                    "整轮P50_ms": round(statistics.median(lat) * 1000, 1) if lat else 0,
                    "整轮最大_ms": round(max(lat) * 1000, 1) if lat else 0,
                    "首字节P50_ms": round(statistics.median(ttfbs), 1) if ttfbs else 0,
                }
                result["问答"].append(item)
                print(f"  {item['项']:<34} 成功 {len(ok):>3}/{len(rows):<3}  "
                      f"整轮 P50 {item['整轮P50_ms']:>8.1f}ms  "
                      f"首字节 P50 {item['首字节P50_ms']:>8.1f}ms")
        else:
            print("  没有可用会话，跳过")

    # ---------- 落盘 ----------
    out = "stress_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n" + "=" * 100)
    print(f"结果已写入 {out}")
    print("=" * 100)


if __name__ == "__main__":
    asyncio.run(main())
