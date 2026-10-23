# -*- coding: utf-8 -*-
"""calculator 白名单契约（2026-10-23 审计补的回归）。

修的是什么
----------
`_CALC_ALLOWED` 定义了却**从未被使用**（全文件只出现定义那一行），
真正生效的是另一条等价正则——注释描述的和代码执行的不是同一件事。
更糟的是两处都放行了英文逗号：

    "1,2"   -> "1,2 = (1, 2)"      # eval 把 1,2 当成元组
    "1,2,3" -> "1,2,3 = (1, 2, 3)"

学生问"1,2 这两个数加起来多少"，工具会一本正经回一个元组。
修法：让常量成为唯一真相（用 `set(expr) <= _CALC_ALLOWED` 校验），并去掉逗号。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tools import calculator  # noqa: E402
from agent.tools import _CALC_ALLOWED  # noqa: E402


def test_comma_is_rejected():
    """★ 逗号必须被拒（修之前会返回元组）。"""
    for bad in ["1,2", "(1,2)", "1,2,3", "3, 4"]:
        out = calculator.invoke({"expression": bad})
        assert "非法表达式" in out, f"{bad!r} 应该被拒，实际返回：{out}"


def test_whitelist_constant_has_no_comma():
    """常量是唯一真相，所以它自己不能含逗号。"""
    assert "," not in _CALC_ALLOWED, f"白名单里还有逗号：{sorted(_CALC_ALLOWED)}"


def test_whitelist_constant_covers_documented_operators():
    """文档承诺支持 + - * / % ** 和括号、小数，常量必须都含。"""
    for ch in "+-*/%().":
        assert ch in _CALC_ALLOWED, f"白名单缺 {ch!r}"


def test_normal_expressions_still_work():
    """回归：改白名单不能误伤正常表达式。"""
    assert calculator.invoke({"expression": "3+5*2"}).endswith("13")
    assert calculator.invoke({"expression": "(10+5)*3"}).endswith("45")
    assert calculator.invoke({"expression": "2**10"}).endswith("1024")
    assert calculator.invoke({"expression": "5%2"}).endswith("1")
    assert calculator.invoke({"expression": "1.5+2.5"}).endswith("4.0")


def test_empty_expression_is_rejected():
    assert "非法表达式" in calculator.invoke({"expression": "   "})


def test_guards_still_hold():
    """原有的护栏不能因为换校验方式而失效。"""
    assert "过长" in calculator.invoke({"expression": "1+" * 40 + "1"})
    assert "不支持连续幂" in calculator.invoke({"expression": "2**3**2"})
    assert "指数过大" in calculator.invoke({"expression": "2**999999"})
    assert "数字过大" in calculator.invoke({"expression": "1" * 20 + "+1"})
    assert "除数不能为零" in calculator.invoke({"expression": "1/0"})
