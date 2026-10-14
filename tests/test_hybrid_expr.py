# -*- coding: utf-8 -*-
"""受限表达式求值器回归（混合架构：条件分支与切换规则的安全底座）。

覆盖三项：
  1. 白名单放行：比较 / 布尔 / 算术 / 下标 / 属性 / len() / .get() 正常求值；
  2. 注入拒绝：任意调用、私有属性、推导式、非 DictView 属性访问一律 ExprError；
  3. DictView 行为与 eval_condition 兜底：异常一律按条件不成立（False）处理。

可从任意位置运行：python tests/test_hybrid_expr.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hybrid.expr import DictView, ExprError, eval_condition, safe_eval  # noqa: E402

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILED.append(msg)


SCOPE = {
    "shared": {
        "score": 0.9,
        "kind": "甲",
        "n": 4,
        "tags": ["x", "y"],
        "nested": {"deep": 7},
        "ok": False,
    },
    "signal": {"error": "节点坏了", "node_id": "a"},
    "ctx": {"goal": "测试", "status": "running", "current_mode": "workflow"},
}


# ==================== 1. 白名单放行 ====================

def test_1_whitelist() -> None:
    """合法表达式按语义求值。"""
    print("\n[1] 白名单表达式求值")
    check(safe_eval("shared.score > 0.8", SCOPE) is True, "属性访问 + 比较")
    check(safe_eval("shared.kind == '甲'", SCOPE) is True, "中文字符串等值")
    check(safe_eval("shared['kind'] == '甲'", SCOPE) is True, "下标访问")
    check(safe_eval("shared.nested.deep == 7", SCOPE) is True, "嵌套属性（递归 DictView）")
    check(safe_eval("shared.n % 2 == 0", SCOPE) is True, "算术取模")
    check(safe_eval("shared.n * 2 + 1 == 9", SCOPE) is True, "四则混合")
    check(safe_eval("shared.score > 0.8 and shared.kind == '甲'", SCOPE) is True, "and 两个都真")
    check(safe_eval("shared.score < 0.8 or shared.kind == '甲'", SCOPE) is True, "or 一真即真")
    check(safe_eval("not shared.ok", SCOPE) is True, "一元 not")
    check(safe_eval("-shared.n == -4", SCOPE) is True, "一元负号")
    check(safe_eval("len(shared.tags) == 2", SCOPE) is True, "len() 列表")
    check(safe_eval("shared.get('missing', 7) == 7", SCOPE) is True, ".get 带默认值")
    check(safe_eval("shared.get('kind') == '甲'", SCOPE) is True, ".get 单参数")
    check(safe_eval("'x' in shared.tags", SCOPE) is True, "in 成员判断")
    check(safe_eval("signal.node_id == 'a'", SCOPE) is True, "signal 作用域注入")
    check(safe_eval("ctx.current_mode == 'workflow'", SCOPE) is True, "ctx 作用域注入")
    check(safe_eval("shared.missing is None", SCOPE) is True, "缺失键 → None，is None 成立")


# ==================== 2. 注入拒绝 ====================

def _raises(expr: str) -> bool:
    try:
        safe_eval(expr, SCOPE)
        return False
    except ExprError:
        return True
    except Exception:
        return False  # 只许抛 ExprError，别的异常说明兜底有洞


def test_2_injection_rejected() -> None:
    """危险/越界表达式一律 ExprError。"""
    print("\n[2] 注入与越界拒绝")
    check(_raises("__import__('os').system('dir')"), "拒绝 __import__ 调用")
    check(_raises("open('x.txt')"), "拒绝 open 调用")
    check(_raises("shared.__class__"), "拒绝 dunder 属性")
    check(_raises("shared._data"), "拒绝下划线私有属性")
    check(_raises("shared.items()"), "拒绝 .get 以外的方法调用")
    check(_raises("'abc'.upper()"), "拒绝非 DictView 的属性访问")
    check(_raises("[c for c in shared.tags]"), "拒绝推导式")
    check(_raises("lambda: 1"), "拒绝 lambda")
    check(_raises("shared.n if shared.ok else 0"), "拒绝三元 IfExp")
    check(_raises("undefined_name == 1"), "拒绝未注入的名字")
    check(_raises("len(1, 2)"), "拒绝多参数 len()")
    check(_raises("shared.n ** 2 == 16"), "拒绝白名单外算术（**）")
    check(_raises(""), "拒绝空表达式")
    check(_raises("shared.score >> 1"), "拒绝位运算")


# ==================== 3. DictView 与 eval_condition 兜底 ====================

def test_3_dictview_and_condition() -> None:
    """DictView 只读行为；eval_condition 任何异常按 False。"""
    print("\n[3] DictView 行为 / eval_condition 兜底")
    dv = DictView({"a": {"b": 2}, "s": "文本"})
    check(dv.a.b == 2, "嵌套属性递归包装")
    check(dv["a"]["b"] == 2, "嵌套下标递归包装")
    check(dv.missing is None, "缺失属性返回 None（不抛 AttributeError 给表达式层）")
    check(dv.get("missing", "dft") == "dft", ".get 默认值")
    check(bool(DictView({})) is False and bool(dv) is True, "空字典布尔为 False")

    readonly = False
    try:
        dv.a = 1
    except ExprError:
        readonly = True
    check(readonly, "赋值被拒绝（只读视图）")

    # eval_condition：异常一律 False，流程不炸
    check(eval_condition("shared.score > 0.8", SCOPE) is True, "成立 → True")
    check(eval_condition("shared.score < 0.8", SCOPE) is False, "不成立 → False")
    check(eval_condition("__import__('os')", SCOPE) is False, "注入表达式 → False（不炸）")
    check(eval_condition("shared.missing > 1", SCOPE) is False, "None 比大小 → False（TypeError 兜底）")
    check(eval_condition("shared.n / 0 == 1", SCOPE) is False, "除零 → None == 1 → False")
    check(eval_condition("", SCOPE) is False, "空条件 → False")


def main() -> int:
    print("=" * 60)
    print("受限表达式求值器回归（白名单 / 注入拒绝 / 兜底）")
    print("=" * 60)
    test_1_whitelist()
    test_2_injection_rejected()
    test_3_dictview_and_condition()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for m in FAILED:
            print("  - " + m)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
