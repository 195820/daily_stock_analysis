# -*- coding: utf-8 -*-
"""
固定事件目录之外的一次性全球大事件（人工登记通道，2026-10-22）

治理规则（与 catalyst_config.GLOBAL_TO_A_SHARE_MAP 的分工）：
- `GLOBAL_TO_A_SHARE_MAP` 是**固定事件目录**（FOMC/CPI/英伟达财报…），只收可复现、
  有固定日历节奏的事件；不得为了让某条小新闻进日历而往关键词表里加行。
- 真正的大事件（产业级技术发布、单次重大并购、突发地缘升级…）**单列一条**登记到本
  模块，而不是塞进关键词映射表——关键词命中无法区分「事件本体」与「蹭名称的旁支
  新闻」（实测：三星 8nm 工艺新闻顶了 SK 海力士财报名头）。
- 新增映射纪律（写进代码注释，不做运行期校验）：recurring 映射只在事件**临近发生**
  时加入 map，且必须同时给 `anchor_keywords` + `event_type_fixed` + `review_note`；
  一次性大事件走本模块。

字段契约（每条必填，缺字段在消费侧按 KeyError 失败即暴露，不静默兜底）：
- event_id         稳定 ID，建议 `manual_YYYYMMDD_<slug>`
- title            事件标题（日历展示用）
- occurred_date    事件发生日（`datetime.date`，不是字符串）
- sectors          A 股映射板块名（与 CONCEPT_UNIVERSE 模糊匹配，未命中即不产事件）
- direction        positive / negative / binary（价格方向）
- category_tag     drift / fade / binary（事件类型，与 direction 正交）
- impact_level     1/2/3，**人工显式给出**，不继承 map 的 base_impact_level
- confidence       0~1，人工显式给出，不继承 map 的 0.8 缺省
- a_share_linkage  A 股传导链条一句话（写入 consensus_expectation）
- source_news      原始新闻标题（供回声检测/归档追溯）
- valid_days       有效期（日历日，缺省 7）：事件在 [occurred_date, +valid_days) 内产事件

只读数据 + 纯函数：无 IO、无 DB、无 LLM。
"""

from datetime import date, timedelta
from typing import Any, Dict, List

# 有效期的缺省天数（事件日之后仍可被日历消费的窗口）
DEFAULT_VALID_DAYS = 7

MANUAL_GLOBAL_EVENTS: List[Dict[str, Any]] = [
    # 示例（按需取消注释并核对板块名/传导链后启用）：
    #
    # {
    #     "event_id": "manual_20261022_deepinfer_open_source",
    #     "title": "[全球·单列] DeepSeek 开源推理框架 DeepInfer 登顶 Agent Arena",
    #     "occurred_date": date(2026, 10, 20),
    #     "sectors": ["AI应用", "算力"],
    #     "direction": "positive",
    #     "category_tag": "fade",
    #     "impact_level": 2,
    #     "confidence": 0.8,
    #     "a_share_linkage": "国产推理框架开源 → 国产算力/推理部署链催化",
    #     "source_news": "DeepSeek开源推理框架DeepInfer，V4.1 Flash登顶Agent Arena开源榜",
    #     "valid_days": 7,
    # },
]


def active_manual_events(today: date) -> List[Dict[str, Any]]:
    """返回 `today` 仍在有效期内的人工单列事件（occurred_date <= today < occurred_date + valid_days）。"""
    active: List[Dict[str, Any]] = []
    for entry in MANUAL_GLOBAL_EVENTS:
        occurred = entry["occurred_date"]
        valid_days = int(entry.get("valid_days", DEFAULT_VALID_DAYS))
        if occurred <= today < occurred + timedelta(days=valid_days):
            active.append(entry)
    return active
