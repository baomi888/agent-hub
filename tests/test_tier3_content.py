# -*- coding: utf-8 -*-
"""第三档回归：网页正文提取（_strip_html）与死代码清理。不联网。"""

import kb.importer as importer
from kb.rag_core import RagPipeline


PAGE = """<html><head><title>测试页</title>
<style>.x{color:red}</style><script>var tracking = 1;</script></head>
<body>
<nav><a href="/">首页</a><a href="/news">新闻</a><a href="/about">关于</a></nav>
<div class="breadcrumb">当前位置：主页 > 文档</div>
<article>
  <h1>向量数据库入门</h1>
  <p>这是正文第一段，讲的是向量数据库的基本原理与使用方法，内容足够长。</p>
  <p>第二段：BM25 是一种经典的关键词排序算法，广泛用于搜索引擎与检索系统。</p>
  <ul><li>列表项一：支持混合检索与 RRF 融合排序</li></ul>
</article>
<aside>相关推荐：广告位招租</aside>
<footer>版权所有 © 2026 某公司 ｜ 隐私政策 ｜ 联系我们</footer>
</body></html>"""


def test_strip_html_keeps_main_content():
    text = importer._strip_html(PAGE)
    assert "向量数据库的基本原理" in text
    assert "BM25 是一种经典的关键词排序算法" in text
    assert "支持混合检索与 RRF 融合排序" in text


def test_strip_html_drops_page_chrome():
    """导航/页脚/侧栏/脚本不再混进正文——它们此前会稀释 embedding 语料和 BM25 词表。"""
    text = importer._strip_html(PAGE)
    assert "首页" not in text
    assert "版权所有" not in text
    assert "广告位" not in text
    assert "tracking" not in text
    # 短噪声块（面包屑等）被长度闸过滤
    assert "当前位置" not in text


def test_strip_html_body_fallback():
    """没有 article/main 的页面退回 body，噪声元素仍然整块剔除。"""
    html = ("<html><body><nav><a href='/'>首页</a></nav>"
            "<div><p>正文内容比较长，足够通过长度过滤这一关，讲的是检索质量。</p></div>"
            "<footer>页脚信息不做入库处理保留原样版权所有</footer></body></html>")
    text = importer._strip_html(html)
    assert "检索质量" in text
    assert "首页" not in text
    assert "页脚信息" not in text


def test_strip_html_unescapes_entities():
    html = "<body><p>Tom &amp; Jerry 的故事，讲了很多年的经典动画片段。</p></body>"
    assert "Tom & Jerry" in importer._strip_html(html)


def test_strip_html_no_tiny_fragment_pollution():
    """单行拼接变成按块分行：行内不出现跨块粘连的碎片。"""
    text = importer._strip_html(PAGE)
    lines = [ln for ln in text.split("\n") if ln]
    assert all(len(ln) >= importer._MIN_BLOCK_CHARS for ln in lines)
    assert len(lines) >= 3  # h1 + 两个 p（ul 行可能与 p 相邻，不强求）


def test_answer_entries_reflect_actual_callers():
    """死代码清理回归（2026-10-16 调整）。

    `answer` 不再是死代码：`POST /api/kb/{kb_id}/query` 一直在调它，2026-10-13 那次清理
    把方法删了却没注意到调用点，导致该端点必然 500，现已按原实现恢复——所以这里必须
    "存在"。`answer_stream` 没有第二个调用方（那个端点是同步接口），仍然保持删除。
    """
    assert hasattr(RagPipeline, "answer")
    assert not hasattr(RagPipeline, "answer_stream")
    # 生产同款格式化器必须保留（api/chat.py 在用）
    assert hasattr(RagPipeline, "_format_refs")
    assert hasattr(RagPipeline, "build_sources")
