#!/usr/bin/env python3
"""一次改完三处版本号，别让它们各说各的。

    python tools/bump_app_version.py 0.2.1 --notes "修了启动屏时长"

会同步改：

  1. `mobile/app.json`      version / android.versionCode / ios.buildNumber
  2. `mobile/version.ts`    编译进 JS 的兜底版本号（读不到嵌入配置时用）
  3. `frontend/public/app-version.json`  线上清单，旧包靠它发现新版

为什么要有这个脚本：
  - `versionCode` 写小了，安卓会拒绝覆盖安装（“应用未安装”），而且不报原因；
  - 清单忘了改，App 里的“检查更新”永远查不到新包；
  - `version.ts` 的兜底值和 app.json 不一致，出问题时显示的版本号是假的。

一次改完，三处天然一致。改完记得提交并推到 main/dev/feat/android-apk ——
CI 会重新出 APK，并把版本号写进 Release 说明。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JSON = ROOT / "mobile" / "app.json"
VERSION_TS = ROOT / "mobile" / "version.ts"
MANIFEST = ROOT / "frontend" / "public" / "app-version.json"

DEFAULT_URL = (
    "https://github.com/baomi888/agent-hub/releases/download/"
    "android-demo/zhisui-android-demo.apk"
)

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="同步三处 App 版本号")
    parser.add_argument("version", help="语义化版本，如 0.2.1")
    parser.add_argument("--code", type=int, help="安卓 versionCode（默认当前值 +1）")
    parser.add_argument("--notes", help="更新说明，显示在 App 的更新弹窗里")
    parser.add_argument("--url", help="新 APK 的 https 下载地址（默认用固定 Release 地址）")
    args = parser.parse_args()

    if not VERSION_RE.match(args.version):
        print(f"版本号要写成 x.y.z，收到的是 {args.version!r}", file=sys.stderr)
        return 2

    app = read_json(APP_JSON)
    expo = app["expo"]
    old_version = expo.get("version", "0.0.0")
    old_code = int(expo.get("android", {}).get("versionCode", 0) or 0)
    code = args.code if args.code is not None else old_code + 1

    if code <= old_code:
        # 覆盖安装会失败，且安卓只报“应用未安装”，极难查 —— 直接挡在这里
        print(f"versionCode 必须递增：现在是 {old_code}，给的是 {code}", file=sys.stderr)
        return 2

    expo["version"] = args.version
    expo.setdefault("android", {})["versionCode"] = code
    expo.setdefault("ios", {})["buildNumber"] = str(code)
    write_json(APP_JSON, app)

    ts = VERSION_TS.read_text(encoding="utf-8")
    ts_new, n = re.subn(
        r'(const VERSION_FALLBACK = ")[^"]*(";)',
        rf"\g<1>{args.version}\g<2>",
        ts,
        count=1,
    )
    if n != 1:
        print(f"没在 {VERSION_TS} 里找到 VERSION_FALLBACK，请手动同步", file=sys.stderr)
        return 1
    VERSION_TS.write_text(ts_new, encoding="utf-8")

    manifest = read_json(MANIFEST) if MANIFEST.exists() else {}
    manifest["version"] = args.version
    manifest["versionCode"] = code
    manifest["url"] = args.url or manifest.get("url") or DEFAULT_URL
    if args.notes:
        manifest["notes"] = args.notes
    elif "notes" not in manifest:
        manifest["notes"] = "修复了一些问题。"
    write_json(MANIFEST, manifest)

    print(f"版本 {old_version} ({old_code}) -> {args.version} ({code})")
    print(f"  {APP_JSON.relative_to(ROOT)}")
    print(f"  {VERSION_TS.relative_to(ROOT)}")
    print(f"  {MANIFEST.relative_to(ROOT)}")
    print("下一步：提交并推送，等 Android APK workflow 跑完；再部署前端让清单生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
