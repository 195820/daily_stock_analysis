# -*- coding: utf-8 -*-
"""板块归属检索证据校验（`evidence_supports_membership`）。

用 2026-10-20 真实检索串钉死两类边界：
- 正例 001400 江顺科技：标题与正文都把「液冷服务器」绑到本票 → 接受
- 反例 601778 晶科科技：唯一出现「军贸」的片段讲的是晶品特装(688084) → 必须拒绝
  （只做 span 存在性校验时这条会误判为正例，本文件即是该反例的回归）
"""

from src.got.sector_evidence import evidence_supports_membership

BOARDS_JINKE = ["军贸概念", "军贸"]
BOARDS_LENG = ["液冷服务", "液冷服务器"]

# ── 真实检索串（Bocha，2026-10-20 探针原样摘录） ──
T_001400_TITLE = "1月7日江顺科技涨停:液冷服务器,有色金属,航天概念热股_股票频道_证券之星"
T_001400_TEXT = (
    "证券之星消息,江顺科技1月7日涨停收盘,收盘价93.87元。该股于10点14分涨停,2次打开涨停。"
    "该股为液冷服务器,有色金属,航天概念热股,当日液冷服务器概念上涨1.39%。"
)
T_601778_NOISE = "晶品特装(688084)智能化编组核心供应商;军贸+内装双轮驱动 - 远瞻慧库"
T_601778_OTHER = (
    "601778,公告总投资245亿元算力项目!上交所火速出手!"
    "当天早些时候,晶科科技公告称,公司与中卫市政府签署投资协议,就宁夏中卫1GW算力中心项目达成合作意向。"
)


def test_positive_title_and_body_bind_same_stock() -> None:
    hit = evidence_supports_membership(
        title=T_001400_TITLE, text=T_001400_TEXT,
        stock_name="江顺科技", code="001400", boards=BOARDS_LENG,
    )
    assert hit is not None
    assert "液冷服务器" in hit


def test_negative_other_stock_snippet_is_rejected() -> None:
    """601778 × 军贸：片段讲的是晶品特装(688084)，必须判否"""
    hit = evidence_supports_membership(
        title=T_601778_NOISE, text=T_601778_NOISE,
        stock_name="晶科科技", code="601778", boards=BOARDS_JINKE,
    )
    assert hit is None


def test_negative_board_in_other_segment_than_stock() -> None:
    """同一结果里：本票出现在一处、板块名出现在另一处（且无指代绑定）→ 判否"""
    hit = evidence_supports_membership(
        title="晶科科技(601778)签下1GW算力中心项目",
        text="公司与中卫市政府签署投资协议。军贸+内装双轮驱动 - 远瞻慧库",
        stock_name="晶科科技", code="601778", boards=BOARDS_JINKE,
    )
    assert hit is None


def test_anaphor_allowed_only_when_title_binds_stock() -> None:
    """正文用「该股」+标题含本票 → 接受；标题不含本票 → 拒绝"""
    ok = evidence_supports_membership(
        title="江顺科技 001400 涨停复盘",
        text="该股为液冷服务器,有色金属,航天概念热股。",
        stock_name="江顺科技", code="001400", boards=BOARDS_LENG,
    )
    assert ok is not None

    bad = evidence_supports_membership(
        title="液冷服务器概念股龙头一览",
        text="该股为液冷服务器概念热股。",
        stock_name="江顺科技", code="001400", boards=BOARDS_LENG,
    )
    assert bad is None


def test_code_format_variants_accepted() -> None:
    hit = evidence_supports_membership(
        title="001400.SZ 江顺科技",
        text="液冷服务器概念成分股 001400.SZ 今日涨停。",
        stock_name="江顺科技", code="001400", boards=BOARDS_LENG,
    )
    assert hit is not None


def test_empty_inputs_return_none() -> None:
    assert evidence_supports_membership(
        title="", text="", stock_name="", code="", boards=BOARDS_LENG
    ) is None
    assert evidence_supports_membership(
        title="江顺科技", text="液冷服务器", stock_name="江顺科技", code="001400", boards=[]
    ) is None


# ── T3：指名式概念列表（000799 实测 F10 页句式） ──

def test_t3_concept_list_accepted_when_title_binds_stock() -> None:
    hit = evidence_supports_membership(
        title="酒鬼酒（000799.SZ）_是什么概念板块_公司简介",
        text="核心概念 白酒概念",
        stock_name="酒鬼酒", code="000799", boards=["白酒", "白酒概念"],
    )
    assert hit is not None
    assert "白酒概念" in hit


def test_t3_rejected_without_title_binding() -> None:
    """标题不绑定本票（泛列表页）→ 概念列表句式也不足以归因"""
    hit = evidence_supports_membership(
        title="白酒概念股一览", text="核心概念 白酒概念",
        stock_name="酒鬼酒", code="000799", boards=["白酒", "白酒概念"],
    )
    assert hit is None


def test_t3_rejected_when_segment_mentions_other_stock() -> None:
    hit = evidence_supports_membership(
        title="晶科科技（601778.SH）_概念板块",
        text="核心概念 军贸概念 688084 晶品特装",
        stock_name="晶科科技", code="601778", boards=["军贸概念", "军贸"],
    )
    assert hit is None


# ── 来源白名单 ──

def test_is_allowed_source_hostname_matching() -> None:
    from src.got.sector_evidence import is_allowed_source

    assert is_allowed_source("https://stock.quote.stockstar.com/corp/business_001400.shtml")
    assert is_allowed_source("https://static.cninfo.com.cn/finalpage/2026-04-02/1225073320.PDF")
    assert is_allowed_source("https://m.hx168.com.cn/stock/F10/000799.html")
    assert is_allowed_source("https://www.dabanke.com/gupiao-000799.html")


def test_is_allowed_source_rejects_lookalike_and_bare_paths() -> None:
    from src.got.sector_evidence import is_allowed_source

    assert not is_allowed_source("https://stockstar.com.evil.cn/x")   # 后缀伪造
    assert not is_allowed_source("https://evil.com/?x=stockstar.com")  # query 里出现域名
    assert not is_allowed_source("")
    assert not is_allowed_source(None)
