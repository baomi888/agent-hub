"""独立测试入口：导入业务代码前隔离数据，禁止访问已有用户数据和外网。"""

from __future__ import annotations

import ipaddress
import os
import runpy
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def configure(root: Path) -> Path:
    sandbox = Path(tempfile.mkdtemp(prefix="zhisui-auth-tests-")).resolve()
    for name in ("data", "chroma", "downloads", "tmp"):
        (sandbox / name).mkdir()
    os.environ.update(
        {
            "DATA_DIR": str(sandbox / "data"),
            "PERSIST_DIR": str(sandbox / "chroma"),
            "DOWNLOAD_DIR": str(sandbox / "downloads"),
            "AGENT_HUB_DB": str(sandbox / "data" / "sessions.db"),
            "AUTH_SECRET": "isolated-tests-only-not-for-deployment",
            "PYTHON_DOTENV_DISABLED": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "ANONYMIZED_TELEMETRY": "False",
            "ALLOW_SIGNUP": "1",
            "ADMIN_USERNAMES": "",
            "ADMIN_UIDS": "",
            "SMTP_PASSWORD": "",
            "DEEPSEEK_API_KEY": "",
            "EMBEDDING_API_KEY": "",
            "VISION_API_KEY": "",
            "ZHISUI_TEST_SANDBOX": str(sandbox),
        }
    )
    tempfile.tempdir = str(sandbox / "tmp")
    os.environ["TEMP"] = os.environ["TMP"] = tempfile.tempdir
    sys.dont_write_bytecode = True
    protected = tuple(root / name for name in ("data", "data-staging", "chroma_db", "downloads"))

    def check_path(raw: object) -> None:
        if not isinstance(raw, (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(raw)).resolve()
        if path == root / ".env" or any(inside(path, base) for base in protected):
            raise PermissionError(f"测试禁止访问已有用户数据或配置：{path}")

    def audit(event: str, args: tuple) -> None:
        if event == "open":
            check_path(args[0])
        elif event == "sqlite3.connect":
            raw = str(args[0])
            if raw.startswith("file:"):
                uri = urlsplit(raw)
                if uri.netloc:
                    raise PermissionError("测试数据库不允许远程 URI")
                raw = unquote(uri.path)
                if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
                    raw = raw[1:]
            if raw != ":memory:" and not inside(Path(raw).resolve(), sandbox):
                raise PermissionError("测试数据库必须位于本次临时目录")
        elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.listdir", "os.scandir"):
            check_path(args[0])
        elif event in ("os.rename", "os.link", "os.symlink"):
            check_path(args[0])
            check_path(args[1])
        elif event in ("socket.connect", "socket.bind"):
            address = args[1]
            if isinstance(address, tuple):
                host = address[0]
                if event == "socket.connect" and address[1] == 8000:
                    raise PermissionError("隔离测试禁止连接可能存在的 8000 端口业务服务")
                if host == "localhost":
                    return
                try:
                    allowed = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    allowed = False
                if not allowed:
                    raise PermissionError("隔离测试只允许本机回环网络")

    sys.addaudithook(audit)
    return sandbox


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sandbox = configure(root)
    sys.path.insert(0, str(root))
    os.chdir(root)
    print(f"独立测试目录：{sandbox}", flush=True)
    if len(sys.argv) > 1 and sys.argv[1] == "--script":
        if len(sys.argv) < 3:
            raise ValueError("--script 需要指定 tests/ 下的测试文件")
        target = (root / sys.argv[2]).resolve()
        if not inside(target, root / "tests") or target.suffix != ".py":
            raise ValueError("只允许执行 tests/ 下的 Python 测试")
        sys.path.insert(0, str(root / "tests"))
        sys.argv = [str(target), *sys.argv[3:]]
        runpy.run_path(str(target), run_name="__main__")
        return 0
    import pytest

    args = sys.argv[1:] or ["tests/test_baseline_environment.py", "-q"]
    return pytest.main(["-o", f"cache_dir={sandbox / 'pytest-cache'}", *args])


if __name__ == "__main__":
    raise SystemExit(main())
