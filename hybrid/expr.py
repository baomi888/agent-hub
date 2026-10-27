"""受限表达式求值器：工作流条件分支与切换规则的 condition 都走这里。

为什么不用 eval/exec：
  条件表达式来自用户配置（工作流定义、切换规则），直接 eval 等于开放
  任意代码执行。这里用 ast 解析后只放行白名单节点类型：
    常量 / 名字（仅限注入的作用域）/ 下标 / 属性（仅 DictView）/
    比较 / 布尔运算 and or / 一元 not - / 算术 + - * / %
    调用仅限 len(...) 与 DictView.get(...)
  其余一律抛 ExprError，宁可条件不生效也不放行注入。

可用作用域（由调用方注入）：
  shared  —— TaskContext.shared 的 DictView 包装，支持 shared.score 与 shared["score"]
  signal  —— 切换信号 payload（DictView）
  ctx     —— 任务上下文只读视图（DictView）
"""

from __future__ import annotations

import ast
from typing import Any


class ExprError(Exception):
    """表达式非法或求值失败。调用方捕获后按"条件不成立"处理。"""


class DictView:
    """dict 的只读视图：属性访问与 .get() 映射到字典取值，递归包装。

    让条件表达式能写 shared.score > 0.8 这种自然形态，
    又不暴露字典自身的真实方法（__class__ 等一律挡掉）。
    """

    def __init__(self, data: dict):
        object.__setattr__(self, "_data", data if isinstance(data, dict) else {})

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name.startswith("__"):
            raise AttributeError(name)
        return _wrap(self._data.get(name))

    def get(self, key: str, default: Any = None) -> Any:
        return _wrap(self._data.get(key, default))

    def __getitem__(self, key: str) -> Any:
        return _wrap(self._data.get(key))

    def __bool__(self) -> bool:
        return bool(self._data)

    def __setattr__(self, name: str, value: Any) -> None:  # 只读
        raise ExprError("条件表达式不允许赋值")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"DictView({self._data!r})"


def _wrap(value: Any) -> Any:
    return DictView(value) if isinstance(value, dict) else value


_ALLOWED_OPS = (
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
)
_ALLOWED_BOOL = (ast.And, ast.Or)
_ALLOWED_BIN = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)


def _eval(node: ast.AST, scope: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, scope)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in scope:
            return scope[node.id]
        raise ExprError(f"未定义的名字：{node.id}")
    if isinstance(node, ast.Subscript):
        target = _eval(node.value, scope)
        key = _eval(node.slice, scope)
        try:
            if isinstance(target, DictView):
                return target[key]
            if isinstance(target, (list, tuple, str)):
                return target[key]
        except (KeyError, IndexError, TypeError):
            return None
        raise ExprError("只允许对 shared/signal/ctx 或序列做下标访问")
    if isinstance(node, ast.Attribute):
        target = _eval(node.value, scope)
        if not isinstance(target, DictView):
            raise ExprError("只允许对 shared/signal/ctx 做属性访问")
        if node.attr.startswith("_"):
            raise ExprError("禁止访问私有属性")
        return getattr(target, node.attr)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, scope)
        if len(node.ops) != 1 or not isinstance(node.ops[0], _ALLOWED_OPS):
            raise ExprError("不支持的比较运算")
        right = _eval(node.comparators[0], scope)
        try:
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
            if isinstance(node.ops[0], ast.Lt):
                return left < right
            if isinstance(node.ops[0], ast.LtE):
                return left <= right
            if isinstance(node.ops[0], ast.Gt):
                return left > right
            if isinstance(node.ops[0], ast.GtE):
                return left >= right
            if isinstance(node.ops[0], ast.In):
                return left in right
            if isinstance(node.ops[0], ast.NotIn):
                return left not in right
            if isinstance(node.ops[0], ast.Is):
                return left is right
            return left is not right
        except TypeError:
            # None 与数字比大小这类：按不成立处理，不炸整个流程
            return False
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, _ALLOWED_BOOL):
            raise ExprError("不支持的布尔运算")
        if isinstance(node.op, ast.And):
            result = True
            for v in node.values:
                result = _eval(v, scope)
                if not result:
                    return result
            return result
        result = False
        for v in node.values:
            result = _eval(v, scope)
            if result:
                return result
        return result
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, scope)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub) and isinstance(operand, (int, float)):
            return -operand
        raise ExprError("不支持的一元运算")
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BIN):
            raise ExprError("不支持的算术运算")
        left, right = _eval(node.left, scope), _eval(node.right, scope)
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            return left % right
        except (TypeError, ZeroDivisionError):
            return None
    if isinstance(node, ast.Call):
        # 只放行 len(x) 与 DictView.get(k[, default])
        if isinstance(node.func, ast.Name) and node.func.id == "len":
            if len(node.args) != 1:
                raise ExprError("len() 只接受一个参数")
            target = _eval(node.args[0], scope)
            try:
                if isinstance(target, DictView):
                    return len(target._data)
                return len(target)
            except TypeError:
                return 0
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            target = _eval(node.func.value, scope)
            if not isinstance(target, DictView):
                raise ExprError("只允许对 shared/signal/ctx 调用 .get()")
            args = [_eval(a, scope) for a in node.args]
            if not 1 <= len(args) <= 2:
                raise ExprError(".get() 参数个数非法")
            return target.get(*args)
        raise ExprError("只允许调用 len() 与 .get()")
    raise ExprError(f"不支持的表达式元素：{type(node).__name__}")


def safe_eval(expr: str, scope: dict[str, Any] | None = None) -> Any:
    """求值受限表达式；非法表达式抛 ExprError。

    scope 里的 dict 会被包成 DictView（属性访问安全），调用方不用自己包。
    """
    if not expr or not expr.strip():
        raise ExprError("空表达式")
    wrapped = {k: _wrap(v) for k, v in (scope or {}).items()}
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except (SyntaxError, ValueError) as e:
        # ValueError：源码含 NUL 等非法字符时 ast.parse 抛的是它而非 SyntaxError
        raise ExprError(f"表达式语法错误：{e}") from e
    except RecursionError as e:
        raise ExprError("表达式嵌套过深") from e
    try:
        return _eval(tree, wrapped)
    except RecursionError as e:
        # 求值阶段递归过深同样收敛成 ExprError，保持"非法表达式抛 ExprError"的契约
        raise ExprError("表达式嵌套过深") from e


def eval_condition(expr: str, scope: dict[str, Any] | None = None) -> bool:
    """条件求值：任何异常都按 False 处理（条件不成立 ≠ 流程崩溃）。

    捕获 Exception 而非仅 ExprError：超深嵌套可能触发 RecursionError、含 NUL 的
    文本会让 ast.parse 抛 ValueError——按本函数的承诺，它们都应退成「条件不成立」，
    而不是把异常抛进编排主循环。KeyboardInterrupt / SystemExit 继承自 BaseException，
    不受影响。
    """
    try:
        return bool(safe_eval(expr, scope))
    except Exception:
        return False
