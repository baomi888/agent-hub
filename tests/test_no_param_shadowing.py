"""不变量：函数体内**不得**用 import 的名字覆盖同名形参。

为什么值得单独钉一条用例
------------------------
2026-10-29 的 `cf49ef2 安全修复` 在 `web_search` 里加了一行
`allow = _configurable(config).get("download_allowlist")`，
而同一个函数体里有一句 `from core import config`。Python 的函数作用域规则是：
**函数内任何位置的 import 都会在局部命名空间创建同名变量**，与 import 写在第几行无关。
于是那个 `config` 形参（框架注入的 RunnableConfig）被 `core.config` **模块对象**顶掉，
`模块.get(...)` 抛 `AttributeError: module 'core.config' has no attribute 'get'`，
被 `except Exception` 吞成「联网搜索暂时不可用」。

代价：**联网搜索整整坏了两天**，用户以为网不好在等，实际每次都在正常请求 Serper、
正常扣费，然后把结果扔掉。排查它花了大半天，因为异常被吞、日志一个字都没有。

这类 bug 的共同特征是"能通过所有单元测试、能通过 lint、代码读起来也没毛病"——
只有形参真的为某个具体值时才炸。所以用静态扫描把这条不变量钉住：
形参名 ∈ 该函数作用域内 import 出来的名字 → 直接失败。

扫描范围只含业务代码（agent/core/kb/api/service/worker），不看 tests 与第三方目录。
"""

import ast
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOTS = ("agent", "core", "kb", "api", "service", "worker")
_SKIP_DIRS = {"__pycache__", "venv", "node_modules", ".git"}


def _params(node: ast.AST) -> set[str]:
    a = node.args
    return {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}


def _shadowing_imports(fn: ast.AST) -> list[tuple[str, int]]:
    """返回该函数作用域内、名字撞上形参的 import（(名字, 行号)）。"""
    params = _params(fn)
    if not params:
        return []
    hits = []
    for sub in ast.walk(fn):
        if isinstance(sub, (ast.Import, ast.ImportFrom)):
            for alias in sub.names:
                name = (alias.asname or alias.name).split(".")[0]
                if name in params:
                    hits.append((name, sub.lineno))
    return hits


def _iter_py_files():
    for root in _ROOTS:
        base = os.path.join(_ROOT, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def test_没有形参被同名_import_覆盖():
    problems = []
    for path in _iter_py_files():
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for name, lineno in _shadowing_imports(node):
                rel = os.path.relpath(path, _ROOT)
                problems.append(f"{rel}:{node.lineno} {node.name}() 的形参 {name} 被第 {lineno} 行的 import 覆盖")
    assert not problems, "形参被同名 import 遮蔽（函数体内 import 会覆盖形参）：\n" + "\n".join(problems)


if __name__ == "__main__":
    test_没有形参被同名_import_覆盖()
    print("OK：没有形参被同名 import 覆盖")
