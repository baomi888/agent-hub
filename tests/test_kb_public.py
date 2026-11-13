"""站内公开库：开关、跨用户只读检索、写路径隔离。

只直接调用 `kb.pipelines` 与 `api.kb` 的函数（显式传 uid，绕过鉴权依赖），
不启 HTTP 服务、不联网、不调真 LLM：embedding 换确定性假实现，
chroma / 数据目录全部落在临时目录。

钉住的是公开语义的边界：
  - 公开只放开「只读检索」（query / 对话挂载 / kb_search 工具）；
  - 文件清单、上传、删除、重建、visibility 开关本身仍然只认 owner；
  - 无权访问与不存在对外长得一样（404），收回公开后他人立即不可再检索。
"""

import asyncio
import io
import os
import sys
import tempfile
import zipfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.config as config  # noqa: E402

# 必须在 import pipelines / rag_core 之前把落盘位置换到临时目录
_TMP = tempfile.mkdtemp(prefix="baomi_kb_public_")
config.PROJECT_ROOT = os.path.join(_TMP, "proj")
config.PERSIST_DIR = os.path.join(_TMP, "proj", "chroma_db")
config.DATA_DIR = os.path.join(_TMP, "proj", "data")
config.DOWNLOAD_DIR = os.path.join(_TMP, "proj", "downloads")
os.makedirs(config.DATA_DIR, exist_ok=True)

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from langchain_core.embeddings import DeterministicFakeEmbedding  # noqa: E402

import api.kb as kb_api  # noqa: E402
import kb.pipelines as pipelines  # noqa: E402
import kb.rag_core as rc  # noqa: E402

rc.get_embeddings = lambda: DeterministicFakeEmbedding(size=64)

OWNER = "u_owner"
OTHER = "u_other"


@pytest.fixture(autouse=True)
def _no_views(monkeypatch):
    """浏览计数在这里一律置空。

    这条文件不换 session 库（它只关心 chroma 与公开登记），详情接口一记浏览
    就会写到仓库里的 data/sessions.db —— 测试污染真实用户数据，
    正是 MEMORY 里反复强调不能做的事。计数的真实行为在 test_kb_views.py 里测。
    """
    monkeypatch.setattr(kb_api.kb_views, "touch", lambda *a, **k: None)
    monkeypatch.setattr(kb_api.kb_views, "count", lambda *a, **k: 0)
    monkeypatch.setattr(kb_api.kb_views, "counts", lambda *a, **k: {})


def _no_quota(monkeypatch):
    monkeypatch.setattr(kb_api.quota, "guard", lambda *a, **k: None)
    monkeypatch.setattr(kb_api.quota, "consume", lambda *a, **k: None)


@pytest.fixture()
def kb_id():
    """库主建一个含真实片段的库，测完连公开登记一起清掉。"""
    kid, _ = pipelines.create_kb(OWNER, "公开测试库")
    yield kid
    pipelines.delete_kb(OWNER, kid)


# ==================== 开关：只有库主能切 ====================


def test_set_public_roundtrip(kb_id):
    assert pipelines.is_public(kb_id) is False  # 新库默认私有

    pipelines.set_public(OWNER, kb_id, True)
    assert pipelines.is_public(kb_id) is True

    pipelines.set_public(OWNER, kb_id, False)
    assert pipelines.is_public(kb_id) is False


def test_set_public_rejects_non_owner(kb_id):
    with pytest.raises(pipelines.NotOwned):
        pipelines.set_public(OTHER, kb_id, True)
    assert pipelines.is_public(kb_id) is False  # 拒绝的同时不能留下半开状态


# ==================== 跨用户只读：放行检索，不放行其余 ====================


def test_public_kb_readable_by_others(kb_id):
    pipelines.set_public(OWNER, kb_id, True)

    # 公开库：别人的进程里也能取到实例、确认存在
    pipe = pipelines.get_or_create(OTHER, kb_id, allow_public=True)
    assert pipe.collection_name == f"kb_{kb_id}"
    assert pipelines.exists(OTHER, kb_id, allow_public=True) is False  # 空库 peek()==0


def test_private_kb_still_rejected_for_others(kb_id):
    # 不公开时 allow_public 也救不了：这是默认拒绝语义，改不得
    with pytest.raises(pipelines.NotOwned):
        pipelines.get_or_create(OTHER, kb_id, allow_public=True)
    assert pipelines.exists(OTHER, kb_id, allow_public=True) is False


def test_delete_kb_clears_public_flag(kb_id):
    pipelines.set_public(OWNER, kb_id, True)
    pipelines.delete_kb(OWNER, kb_id)
    assert pipelines.is_public(kb_id) is False
    # 残留标记也不该出现在公开列表里（无主库谁也检索不了）
    assert all(k["kb_id"] != kb_id for k in pipelines.list_public())


# ==================== API 层：query 放行，其余仍 owner-only ====================


def test_query_open_to_others_when_public(monkeypatch, kb_id):
    _no_quota(monkeypatch)
    pipelines.set_public(OWNER, kb_id, True)
    # LLM 问答换桩：这里只验证「门放行了、拿到的是这个库的 pipe」
    monkeypatch.setattr(
        kb_api.pipelines,
        "get_or_create",
        lambda owner, kid, **k: SimpleNamespace(
            answer=lambda q, top_k: (f"answered for {owner}", "refs"), peek=lambda: 1
        ),
    )
    out = asyncio.run(kb_api.query_kb(kb_id, "什么是公开库", 3, OTHER))
    assert out["answer"] == "answered for u_other"


def test_query_private_returns_404_for_others(monkeypatch, kb_id):
    _no_quota(monkeypatch)
    with pytest.raises(HTTPException) as e:
        asyncio.run(kb_api.query_kb(kb_id, "悄悄看一眼", 3, OTHER))
    assert e.value.status_code == 404  # 404 而非 403：不泄露存在性


def test_files_and_visibility_stay_owner_only(monkeypatch, kb_id):
    """公开放开的是检索，不是整个库：文件级接口与开关仍只认库主。"""
    _no_quota(monkeypatch)
    pipelines.set_public(OWNER, kb_id, True)

    with pytest.raises(HTTPException):
        asyncio.run(kb_api.list_files(kb_id, OTHER))
    with pytest.raises(HTTPException):
        asyncio.run(kb_api.set_kb_visibility(kb_id, kb_api.VisibilityRequest(public=False), OTHER))

    # 库主自己不受影响。返回里多了 desc：广场卡片要用它显示简介
    out = asyncio.run(kb_api.set_kb_visibility(kb_id, kb_api.VisibilityRequest(public=False), OWNER))
    # 返回多了 access：前端保存后要立刻回显"仅观看/可下载"的选择
    assert out == {"kb_id": kb_id, "public": False, "desc": "", "access": "preview"}


def test_public_list_excludes_caller_and_private(monkeypatch, kb_id):
    _no_quota(monkeypatch)
    pipelines.set_public(OWNER, kb_id, True)

    mine = asyncio.run(kb_api.list_public_kbs(OWNER))
    assert mine["kbs"] == []  # 自己的公开库不重复出现（自己的列表里已经有了）

    theirs = asyncio.run(kb_api.list_public_kbs(OTHER))
    assert [k["kb_id"] for k in theirs["kbs"]] == [kb_id]
    # 字段集合钉死：广场要展示的资料够用，且**没有**文件清单。
    # 一旦有人往这里顺手加了 files 明细、owner uid 之类，这条会红。
    # official 是 2026-11-13 加的唯一新字段：前端靠它把官方课程库单独分一区。
    # posts 是同一天加的留言条数（卡片上「留言 N」用）—— 只给数量，同样不含清单。
    assert all(
        set(k) == {
            "kb_id",
            "name",
            "chunks",
            "files",
            "owner_name",
            "desc",
            "at",
            "official",
            "posts",
            # 2026-11-13 新增：access=别人能做什么（preview/download），
            # views=看过的人数（独立访客）。两个都是展示用，仍不含清单。
            "access",
            "views",
        }
        for k in theirs["kbs"]
    )

    pipelines.set_public(OWNER, kb_id, False)
    assert asyncio.run(kb_api.list_public_kbs(OTHER))["kbs"] == []


# ==================== 广场化：简介与展示用元信息（2026-11-13） ====================


def test_public_desc_roundtrip(kb_id):
    """简介写得进、读得出；改简介不动分享时间；不传 desc 不覆盖已有简介。"""
    pipelines.set_public(OWNER, kb_id, True, "考研数学一，近十年真题按章节整理")
    item = next(k for k in pipelines.list_public() if k["kb_id"] == kb_id)
    assert item["desc"] == "考研数学一，近十年真题按章节整理"
    assert item["at"] > 0
    first_at = item["at"]

    # 只改开关（desc=None）不能把用户写的话抹掉
    pipelines.set_public(OWNER, kb_id, True)
    item = next(k for k in pipelines.list_public() if k["kb_id"] == kb_id)
    assert item["desc"] == "考研数学一，近十年真题按章节整理"
    # 改简介不该让「分享于」变成今天
    assert item["at"] == first_at

    # 显式传空串才是清空
    pipelines.set_public(OWNER, kb_id, True, "")
    item = next(k for k in pipelines.list_public() if k["kb_id"] == kb_id)
    assert item["desc"] == ""


def test_withdraw_clears_registry_entry(kb_id):
    """收回 = 整条登记删掉，包括简介。

    这是刻意的取舍：留着 on=False 的壳可以省掉"下次再分享要重写简介"，
    但那样「在表里」就不等于「公开」了，所有读点都得再判一次 on，
    漏一处就把私有库当公开。安全边界不拿便利换。
    """
    pipelines.set_public(OWNER, kb_id, True, "计算机组成原理笔记")
    pipelines.set_public(OWNER, kb_id, False)
    assert pipelines.is_public(kb_id) is False
    mine = pipelines.list_all(OWNER)
    row = next(k for k in mine if k["kb_id"] == kb_id)
    assert row["public"] is False
    assert row["public_desc"] == ""  # 跟着登记一起没了
    # 也不能在广场上留一行
    assert all(k["kb_id"] != kb_id for k in pipelines.list_public())


def test_legacy_bool_format_still_loads(kb_id):
    """旧登记表 {"kb_id": true} 要能被读成新结构，不能因为格式变了就整表丢光。"""
    import json

    path = pipelines._public_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump({kb_id: True}, f)
    pipelines._KB_PUBLIC_MTIME = None  # 逼它重读
    pipelines._load_public()
    assert pipelines.is_public(kb_id) is True
    item = next(k for k in pipelines.list_public() if k["kb_id"] == kb_id)
    assert item["desc"] == ""  # 旧数据没有简介，不编一个出来
    assert item["at"] == 0  # 也没有分享时间，前端会显示「较早分享」


def test_list_public_counts_files_without_leaking_names(kb_id):
    """广场要文件数，但只能给数量 —— 名字一个都不能出去。"""
    pipelines.add_file_records(
        OWNER,
        kb_id,
        [
            {"name": "真题2015.pdf", "chunks": 12, "added_at": 1},
            {"name": "错题本.md", "chunks": 5, "added_at": 2},
        ],
    )
    pipelines.set_public(OWNER, kb_id, True)
    item = next(k for k in pipelines.list_public() if k["kb_id"] == kb_id)
    assert item["files"] == 2
    assert "真题2015.pdf" not in str(item)
    assert "错题本.md" not in str(item)

# ==================== 官方课程库（2026-11-13）====================
#
# 官方库是"公开"的一种，但多放开一件事：别人能**看文件清单、下载原文**。
# 这条边界只对 official 库生效 —— 普通用户分享的库依旧是"只给份数"。
# 所以这里的测试分两组：官方库该通的要通，非官方库该 404 的必须 404。


OFFICIAL = pipelines.OFFICIAL_OWNER


def _put_source(kb_id: str, name: str, data: bytes) -> None:
    """把一份资料落到该库的原始文件目录（source_path 的哈希命名规则照用）。"""
    path = pipelines.source_path(kb_id, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _docx_bytes(text: str) -> bytes:
    """造一个最小可解析的 docx：只要有 word/document.xml 就够 load_document 用。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return buf.getvalue()


@pytest.fixture()
def official_kb():
    """一门官方课：归属 official、公开、带 official 标记。"""
    kid, _ = pipelines.create_kb(OFFICIAL, "高等数学")
    pipelines.set_public(OFFICIAL, kid, True, "各章单元测试与答案", official=True)
    yield kid
    pipelines.delete_kb(OFFICIAL, kid)


def test_official_flag_roundtrip(official_kb):
    assert pipelines.is_official(official_kb) is True
    item = next(k for k in pipelines.list_public() if k["kb_id"] == official_kb)
    assert item["official"] is True
    assert item["desc"] == "各章单元测试与答案"

    # 收回公开 = 官方身份一起没（标记就存在公开登记表里，没有第二处）
    pipelines.set_public(OFFICIAL, official_kb, False)
    assert pipelines.is_official(official_kb) is False
    assert all(k["kb_id"] != official_kb for k in pipelines.list_public())


def test_visibility_api_cannot_mint_official(kb_id):
    """用户没法把自己的库伪装成官方。

    这是本题最要紧的一条：official 只能由服务端导入脚本写。
    /visibility 的请求体里根本没有这个字段，所以传什么都变不出官方身份。

    2026-11-13 起行为变了一半：**同学公开的库也能看清单了**（用户要求），
    但"能看清单"不等于"是官方"——详情里 official 仍是 False、
    署名不是"官方"、默认只给看不能下。这三条就是防伪装的落点。
    """
    out = asyncio.run(
        kb_api.set_kb_visibility(kb_id, kb_api.VisibilityRequest(public=True, desc="假装官方"), OWNER)
    )
    assert out["public"] is True
    assert pipelines.is_official(kb_id) is False

    # 请求体里塞 official 也没用：模型里没这个字段，多传的被丢掉
    asyncio.run(
        kb_api.set_kb_visibility(
            kb_id,
            kb_api.VisibilityRequest(public=True, **{"official": True}),  # type: ignore[arg-type]
            OWNER,
        )
    )
    assert pipelines.is_official(kb_id) is False

    detail = asyncio.run(kb_api.get_official_kb(kb_id, OTHER))
    assert detail["official"] is False
    assert detail["owner_name"] != "官方"
    # 默认仅观看：清单里每份资料都不给下载
    assert detail["access"] == "preview"
    assert all(f["download"] is False for f in detail["files"])


def test_preview_only_blocks_download_but_allows_inline(kb_id):
    """仅观看：`/file` 不带 inline 必须 403，带 inline 放行。

    403 与 404 的区别在这里是刻意的：库是公开的、名字也是真的，
    只是不许下载 —— 说"不存在"会让用户以为文件丢了。
    """
    asyncio.run(kb_api.set_kb_visibility(kb_id, kb_api.VisibilityRequest(public=True), OWNER))
    with pytest.raises(HTTPException) as e:
        asyncio.run(kb_api.get_official_file(kb_id, "第一章.pdf", 0, OTHER))
    assert e.value.status_code == 403
    assert "仅观看" in e.value.detail

    # inline=1 过了那道闸，往下走才发现文件不存在 → 404（不是 403）
    with pytest.raises(HTTPException) as e2:
        asyncio.run(kb_api.get_official_file(kb_id, "第一章.pdf", 1, OTHER))
    assert e2.value.status_code == 404


def test_download_allowed_when_owner_opts_in(kb_id):
    """库主开了"可下载"：下载不再被 403 挡住（文件不存在仍是 404）。"""
    asyncio.run(
        kb_api.set_kb_visibility(
            kb_id, kb_api.VisibilityRequest(public=True, access="download"), OWNER
        )
    )
    with pytest.raises(HTTPException) as e:
        asyncio.run(kb_api.get_official_file(kb_id, "第一章.pdf", 0, OTHER))
    assert e.value.status_code == 404  # 不是 403：闸已经放行，卡在"没这个文件"


def test_private_kb_detail_still_404(kb_id):
    """只观看不等于所有人能看：没公开的库，详情接口照样 404。"""
    with pytest.raises(HTTPException) as e:
        asyncio.run(kb_api.get_official_kb(kb_id, OTHER))
    assert e.value.status_code == 404


def test_official_kb_is_not_in_any_users_list(official_kb):
    """官方库不出现在任何人的"我的知识库"里（owner 不是真实账号）。"""
    assert all(k["kb_id"] != official_kb for k in pipelines.list_all(OWNER))
    assert all(k["kb_id"] != official_kb for k in pipelines.list_all(OTHER))


def test_official_detail_lists_files_with_size(official_kb):
    name = "高等数学（一）单元测试/第一章试卷.docx"
    _put_source(official_kb, name, _docx_bytes("第一章 极限与连续"))
    pipelines.add_file_records(OFFICIAL, official_kb, [{"name": name, "chunks": 3, "added_at": 1}])

    out = asyncio.run(kb_api.get_official_kb(official_kb, OTHER))
    assert out["name"] == "高等数学"
    assert out["official"] is True
    assert out["owner_name"] == "官方"
    assert out["desc"] == "各章单元测试与答案"

    f = out["files"][0]
    assert f["name"] == name
    assert f["size"] > 0
    assert f["ext"] == "docx"
    assert f["preview"] is True and f["download"] is True


def test_official_file_serves_bytes_and_inline_flag(official_kb):
    name = "复习题与答案/第一章.pdf"
    _put_source(official_kb, name, b"%PDF-1.4\n%fake\n")
    pipelines.add_file_records(OFFICIAL, official_kb, [{"name": name, "chunks": 0, "added_at": 1}])

    dl = asyncio.run(kb_api.get_official_file(official_kb, name, 0, OTHER))
    assert dl.headers["content-disposition"].startswith("attachment")
    assert dl.media_type == "application/pdf"

    inline = asyncio.run(kb_api.get_official_file(official_kb, name, 1, OTHER))
    assert inline.headers["content-disposition"].startswith("inline")

    # 清单里没有的名字一律 404：白名单是文件清单，不是路径拼接
    for bad in ("../data/sessions.db", "不存在的资料.pdf", ""):
        with pytest.raises(HTTPException) as e:
            asyncio.run(kb_api.get_official_file(official_kb, bad, 0, OTHER))
        assert e.value.status_code == 404


def test_official_text_preview(official_kb):
    name = "说明/README.md"
    _put_source(official_kb, name, "这门课的官方资料说明。".encode())
    pipelines.add_file_records(OFFICIAL, official_kb, [{"name": name, "chunks": 1, "added_at": 1}])

    out = asyncio.run(kb_api.get_official_file_text(official_kb, name, OTHER))
    assert "官方资料说明" in out["content"]
    assert out["truncated"] is False


def test_loader_reads_docx_csv_jsonl(tmp_path):
    """三种新格式都要能抽出文字 —— 官方库的 Word / 题库 / 语料全靠它建索引。"""
    docx = tmp_path / "a.docx"
    docx.write_bytes(_docx_bytes("第一章 极限与连续"))
    assert "极限与连续" in rc.load_document(str(docx))[0].page_content

    csvp = tmp_path / "answers.csv"
    csvp.write_text("题号,答案\n1,B\n", encoding="utf-8")
    text = rc.load_document(str(csvp))[0].page_content
    assert "题号：1" in text and "答案：B" in text

    jsonl = tmp_path / "q.jsonl"
    jsonl.write_text('{"题目":"极限的定义","答案":"ε-δ 语言"}\n坏行原样保留\n', encoding="utf-8")
    text = rc.load_document(str(jsonl))[0].page_content
    assert "题目：极限的定义" in text
    assert "坏行原样保留" in text  # 坏行不能丢：宁可难看，也别悄悄吞资料


# ==================== 数学符号与预览保真（2026-11-13 晚）====================
#
# 三件事各自独立，但都是"学生看到乱码"的成因：
#   1. PDF 里的希腊字母/运算符是字体私有区字符（U+F061 = α），必须还原；
#   2. Word 里的公式是 OLE 图片对象，抽文字拿不到，要留〔公式〕标记；
#   3. 预览要能读到"原版 PDF"，而不是抽出来的残缺正文。


def test_math_text_restores_symbol_font():
    from kb import math_text

    raw = "向量\uF061\uF062线性无关，且\uF0F7\uF0F7"
    out = math_text.clean_text(raw)
    assert "α" in out and "β" in out
    # 矩阵括号拼块压成一个，不能让 0xF0F7 漏到正文里
    assert "\uF0F7" not in out
    assert math_text.garble_ratio(out) == 0.0


def test_math_text_distinguishes_mt_extra_codes():
    from kb import math_text

    # 0x61 在 Symbol 里是 α，在同一份 PDF 的 MT Extra 里是括号竖条：
    # 给了 mt_codes 就必须按 MT Extra 解，不能一律当希腊字母
    assert math_text.decode_pua("\uF061") == "α"
    assert math_text.decode_pua("\uF061", mt_codes={0x61}) != "α"


def test_docx_formula_placeholder(tmp_path):
    """Word 里的公式对象不能凭空消失，至少要留下〔公式〕。"""
    path = tmp_path / "with-formula.docx"
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' xmlns:o="urn:schemas-microsoft-com:office:office"'
        ' xmlns:v="urn:schemas-microsoft-com:vml">'
        "<w:body><w:p>"
        "<w:r><w:t>5．选（C）因函数</w:t></w:r>"
        "<w:r><w:object><v:shape><v:imagedata/></v:shape>"
        '<o:OLEObject ProgID="Equation.DSMT4"/></w:object></w:r>'
        "<w:r><w:t>可导，则</w:t></w:r>"
        "</w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)
    text = rc.load_document(str(path))[0].page_content
    assert "〔公式〕" in text
    assert "选（C）" in text and "可导" in text


def test_official_detail_exposes_preview_pdf(official_kb):
    """Word 资料配了原版 PDF 时：清单给出 preview_file，且该文件读得到。"""
    name = "高等数学（一）单元测试/第一章试卷.docx"
    preview = "__preview__/" + name + ".pdf"
    _put_source(official_kb, name, _docx_bytes("第一章 极限"))
    _put_source(official_kb, preview, b"%PDF-1.4\n%preview\n")
    pipelines.add_file_records(
        pipelines.OFFICIAL_OWNER,
        official_kb,
        [{"name": name, "chunks": 3, "added_at": 1, "preview": preview}],
    )

    out = asyncio.run(kb_api.get_official_kb(official_kb, OTHER))
    f = next(x for x in out["files"] if x["name"] == name)
    assert f["preview_file"] == preview
    assert f["preview"] is True

    # 预览 PDF 不在清单里单列一行，但必须能读（否则预览 404）
    resp = asyncio.run(kb_api.get_official_file(official_kb, preview, 0, OTHER))
    assert resp.media_type == "application/pdf"

    # 清单外仍然 404：preview 只是"这条记录的附属文件"，不是任意路径
    with pytest.raises(HTTPException) as e:
        asyncio.run(kb_api.get_official_file(official_kb, "__preview__/随便.pdf", 0, OTHER))
    assert e.value.status_code == 404
