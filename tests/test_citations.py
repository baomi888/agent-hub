"""正文引用清理（core/citations.py）的回归。

背景：模型会跟着会话里的历史回答继续写 `[1]` 与末尾「依据：[1][2]」，即使这一轮
既没检索也没联网。界面上那是几个点了没反应的小方块，用户会当成坏功能。
"""

from core.citations import strip_phantom_citations


def test_strips_all_markers_when_no_source():
    text = "- 牛杂：本地攻略推荐 [1]\n- 鸭粥：招牌 [2]\n\n依据：[1][2]"
    out = strip_phantom_citations(text, has_kb=False, web_ids=set())
    assert out == "- 牛杂：本地攻略推荐\n- 鸭粥：招牌"


def test_bold_basis_line_removed_entirely():
    assert strip_phantom_citations("正文[1]\n\n**依据：[1]**", has_kb=False) == "正文"


def test_keeps_web_marks_that_have_real_sources():
    out = strip_phantom_citations(
        "结论甲 [W1]，结论乙 [W2]", has_kb=False, web_ids={1}
    )
    assert out == "结论甲 [W1]，结论乙"


def test_keeps_kb_marks_when_kb_has_sources():
    text = "甲 [1] 乙 [2] 丙 [7]"
    # 有资料库来源时不做编号精确删除（refs 是文本，精确解析在前端）
    assert strip_phantom_citations(text, has_kb=True) == text


def test_markdown_link_not_treated_as_citation():
    text = "见 [官网](https://a.example/x) 说明"
    assert strip_phantom_citations(text, has_kb=False) == text


def test_mixed_two_numbering_systems_independent():
    out = strip_phantom_citations(
        "网页 [W1] 资料库 [1] 编的 [3]",
        has_kb=False,
        web_ids={1},
    )
    # 没有资料库来源 ⇒ [1] 与 [3] 都是编的
    assert out == "网页 [W1] 资料库 编的"


def test_plain_text_untouched():
    text = "纯文本\n\n第二段"
    assert strip_phantom_citations(text, has_kb=False) == text


def test_adjacent_markers_leave_no_empty_brackets():
    assert strip_phantom_citations("结论 [1][2][3]", has_kb=False) == "结论"


def test_english_sources_line():
    assert strip_phantom_citations("answer [1]\n\nSources: [1]", has_kb=False) == "answer"


def test_basis_line_with_extra_words_keeps_line():
    out = strip_phantom_citations("依据：[1]，另见官方公告", has_kb=False)
    assert out == "依据：，另见官方公告"


def test_empty_input():
    assert strip_phantom_citations("", has_kb=False) == ""
