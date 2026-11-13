#!/usr/bin/env python
"""把整理好的官方课程材料导入成「官方公开知识库」。

一份材料 = 一门课一个库，库里保留**原始文件**（可预览、可下载），
同时抽取正文建索引（可被对话检索）。归属固定为 `pipelines.OFFICIAL_OWNER`
——不是真实账号，只占一个 owner 值，所以不会污染任何学生的"我的知识库"。

用法（在仓库根目录，用后端 venv）：

    # 先空跑，只校验清单与文件，不写库、不花 embedding 费用
    venv/Scripts/python tools/import_official_kb.py --src <staging目录> --dry-run

    # 正式导入（可重复执行：已入库的文件默认跳过）
    venv/Scripts/python tools/import_official_kb.py --src <staging目录>

    # 只重导某一门课，并把它的片段全部重建
    venv/Scripts/python tools/import_official_kb.py --src <staging> --only 高等数学 --force

staging 目录结构：

    courses.json           # [{key, name, desc, dir, files: [相对路径…]}]
    files/<key>/<相对路径>

设计取舍：
  - **逐份容错**：某一份 Word 解析失败（比如扫描件）不该让整门课导不进去。
    失败的那份仍然登记（chunks=0）并保留原件，界面上显示"未做文字检索"，
    学生照样能下载——这比整门课 500 更接近"资料可用"的目标。
  - **重跑不重复花钱**：已入库且 chunks>0 的文件默认跳过；--force 才重建。
  - **清单即白名单**：库里登记的文件名 = 接口允许读的文件名，
    手工往 data/kb_sources 里塞文件不会自动出现在公开库上。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kb import pipelines  # noqa: E402
from kb.rag_core import load_document  # noqa: E402

DEFAULT_CHUNK = 500
DEFAULT_OVERLAP = 50
DESC_MAX = 200


def _read_manifest(src: str) -> list[dict]:
    path = os.path.join(src, "courses.json")
    if not os.path.exists(path):
        raise SystemExit(f"找不到清单：{path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        raise SystemExit("courses.json 应该是一个非空数组")
    for c in data:
        for k in ("key", "name", "desc", "dir", "files"):
            if k not in c:
                raise SystemExit(f"courses.json 缺字段 {k}：{c}")
        if len(c["desc"]) > DESC_MAX:
            raise SystemExit(f"「{c['name']}」简介 {len(c['desc'])} 字，超过 {DESC_MAX}")
    return data


def _file_path(src: str, course: dict, rel: str) -> str:
    return os.path.join(src, course["dir"].replace("/", os.sep), rel.replace("/", os.sep))


def _preview_chars(path: str) -> int:
    """建索引前先试读一次：字数、以及"能不能读出文字"。

    这一步和后面 build_index 的解析是同一份代码（load_document），
    所以这里读到多少字，就是真正会进库的内容量——预览数字不会说谎。
    """
    docs = load_document(path)
    return sum(len((d.page_content or "").strip()) for d in docs)


def import_course(src: str, course: dict, *, force: bool, dry: bool) -> dict:
    name = course["name"]
    kb_id = pipelines.new_kb_id(pipelines.OFFICIAL_OWNER, name)
    files = course["files"]
    print(f"\n=== {name}（kb_id={kb_id}，{len(files)} 份资料）===")
    if dry:
        total = 0
        for rel in files:
            path = _file_path(src, course, rel)
            if not os.path.exists(path):
                print(f"  [缺文件] {rel}")
                continue
            try:
                n = _preview_chars(path)
                total += n
                print(f"  {rel}  {n:,} 字")
            except Exception as e:
                print(f"  {rel}  [抽取失败] {e}")
        return {"name": name, "kb_id": kb_id, "files": len(files), "chars": total, "written": 0}

    # 建库（幂等：同名同主会算出一样的 kb_id，重复调用只是把注册表再写一遍）
    pipelines.create_kb(pipelines.OFFICIAL_OWNER, name)
    pipelines.ensure_source_dir(kb_id)
    floor = pipelines.get_files(pipelines.OFFICIAL_OWNER, kb_id)
    have = {r.get("name"): r for r in floor if r.get("chunks")}

    # 清单里已经不存在的旧文件：连片段带原件一起清掉，否则公开库上会留着幽灵条目
    stale = [n for n in have if n not in set(files)]
    if stale:
        pipe = pipelines.get_or_create(pipelines.OFFICIAL_OWNER, kb_id)
        for n in stale:
            try:
                pipe.delete_by_source(n)
            except Exception:
                pass
            try:
                p = pipelines.source_path(kb_id, n)
                if os.path.exists(p):
                    os.unlink(p)
            except OSError:
                pass
            pipelines.remove_file_record(pipelines.OFFICIAL_OWNER, kb_id, n)
        print(f"  清掉 {len(stale)} 份已不在清单里的旧资料")

    now = int(time.time())
    records: list[dict] = []
    first_write = True
    written = 0
    for i, rel in enumerate(files, 1):
        path = _file_path(src, course, rel)
        tag = f"  [{i}/{len(files)}] {rel}"
        if not os.path.exists(path):
            print(tag + "  [缺文件，跳过]")
            continue

        # 原件先落盘：即使正文抽不出来，下载也必须能下
        dest = pipelines.source_path(kb_id, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(path, dest)
        size = os.path.getsize(dest)

        if not force and rel in have:
            chunks = int(have[rel].get("chunks") or 0)
            records.append(
                {
                    "name": rel,
                    "chunks": chunks,
                    "added_at": have[rel].get("added_at") or now,
                    "origin": "official",
                    "size": size,
                    "ext": os.path.splitext(rel)[1].lower().lstrip("."),
                }
            )
            print(tag + f"  已入库（{chunks} 段），原件已刷新")
            continue

        t0 = time.time()
        try:
            with pipelines.kb_lock(kb_id):
                pipe = pipelines.get_or_create(pipelines.OFFICIAL_OWNER, kb_id)
                chunks = pipe.build_index(
                    DEFAULT_CHUNK,
                    DEFAULT_OVERLAP,
                    [dest],
                    # force 时第一份清库重建；否则一律追加（旧片段由 stale 逻辑处理）
                    mode=("rebuild" if (force and first_write) else "append"),
                    source_names=[rel],
                )
            written += 1
            first_write = False
            print(tag + f"  {chunks} 段（{time.time() - t0:.1f}s）")
        except Exception as e:
            # 扫描件 / 解析失败：登记 0 段，原件仍在，界面会标"未做文字检索"
            chunks = 0
            print(tag + f"  [未建索引] {e}")

        records.append(
            {
                "name": rel,
                "chunks": chunks,
                "added_at": now,
                "origin": "official",
                "size": size,
                "ext": os.path.splitext(rel)[1].lower().lstrip("."),
            }
        )

    # 只保留本次清单里的记录：旧文件已在上面删掉，这里再兜一次底
    pipelines.retain_file_records(pipelines.OFFICIAL_OWNER, kb_id, {r["name"] for r in records})
    if records:
        pipelines.add_file_records(pipelines.OFFICIAL_OWNER, kb_id, records)

    # 公开 + 官方标记。★ 这是全站唯一的"官方"写入点：接口层没有透传这个字段的路径
    pipelines.set_public(pipelines.OFFICIAL_OWNER, kb_id, True, course["desc"], official=True)
    chunks_total = pipelines.get_or_create(pipelines.OFFICIAL_OWNER, kb_id).count()
    print(f"  合计 {chunks_total:,} 段已建库，公开状态：是（官方）")
    return {
        "name": name,
        "kb_id": kb_id,
        "files": len(records),
        "chars": 0,
        "written": written,
        "chunks": chunks_total,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="导入官方公开知识库")
    ap.add_argument("--src", required=True, help="staging 目录（含 courses.json 与 files/）")
    ap.add_argument("--only", action="append", default=[], help="只导这些课程（课程名或 key）")
    ap.add_argument("--force", action="store_true", help="已入库的文件也重建索引")
    ap.add_argument("--dry-run", action="store_true", help="只校验与统计，不写库")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    manifest = _read_manifest(src)
    if args.only:
        keys = set(args.only)
        manifest = [c for c in manifest if c["name"] in keys or c["key"] in keys]
        if not manifest:
            raise SystemExit(f"--only 没有匹配到课程：{args.only}")

    if not args.dry_run:
        pipelines.warmup()

    results = []
    for c in manifest:
        results.append(import_course(src, c, force=args.force, dry=args.dry_run))

    print("\n==== 汇总 ====")
    for r in results:
        extra = f"{r['written']} 份新建索引" if not args.dry_run else f"{r['chars']:,} 字"
        tail = f" · {r.get('chunks', 0):,} 段" if not args.dry_run else ""
        print(f"  {r['name']:<10} {r['files']:>2} 份 · {extra}{tail}")
    if args.dry_run:
        print("（dry-run，没有写入任何数据）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
