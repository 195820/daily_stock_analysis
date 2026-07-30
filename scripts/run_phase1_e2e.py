"""
Phase 1 端到端测试脚本

使用当天真实数据跑通 GOT 完整流程：
  Node 1 (大盘) → Node 2 (板块) → Node 3 (资金面)
  Node 4 (消息面) ‖ Node 5 (情绪面)
  → Node 6 (聚合)

运行方式：
  conda activate dsa
  python scripts/run_phase1_e2e.py
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path

# 确保项目根目录在 sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

sys.stdout.reconfigure(encoding="utf-8")

# 确保代理环境变量生效（push2 东财 API 需要代理绕过 TLS 指纹检测）
# 如果系统已设置 HTTP_PROXY/HTTPS_PROXY，直接使用；否则不额外配置

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase1_e2e")


def _print_section(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


async def run_phase1_e2e():
    """Phase 1 端到端真实数据测试"""
    start_time = time.time()

    # 反爬补丁预加载
    try:
        from src.patches.eastmoney_patch import eastmoney_patch
        eastmoney_patch()
        logger.info("东财反爬补丁已加载")
    except Exception as e:
        logger.warning(f"反爬补丁加载失败: {e}")

    _print_section(f"Phase 1 端到端测试 — {date.today().isoformat()}")

    # 导入编排器
    from src.got import GOTOrchestrator

    # 创建编排器（真实数据模式）
    orchestrator = GOTOrchestrator(use_mock=False)

    # 运行两路并行 + 聚合
    issues: list[str] = []

    try:
        unified = await orchestrator.run()
    except Exception as e:
        logger.error(f"GOT 编排器执行失败: {e}")
        traceback.print_exc()
        issues.append(f"编排器异常: {e}")
        unified = None

    elapsed = time.time() - start_time

    # 打印详细结果
    if unified:
        _print_section("Node 6 聚合结果")

        print(f"\n  状态:        {unified.status}")
        print(f"  统一信号:    {unified.unified_signal}")
        print(f"  综合置信度:  {unified.overall_confidence:.2f}")
        print(f"  仓位上限:    {unified.final_position_limit:.0%}")
        print(f"  共同根因:    {unified.common_root_cause or '无'}")

        # 矛盾分析
        _print_section("矛盾分析")
        if unified.contradictions:
            for i, c in enumerate(unified.contradictions, 1):
                print(f"\n  矛盾 {i}: {c.pair} [{c.severity}] ({c.timeframe})")
                print(f"    描述: {c.description}")
                print(f"    解决: {c.resolution}")
                print(f"    置信度调整: {c.confidence_adjustment:+.2f}")
        else:
            print("  无矛盾")

        # 消息面覆盖
        _print_section("消息面覆盖")
        if unified.news_override:
            no = unified.news_override
            print(f"  触发覆盖: {no.override}")
            print(f"  原始仓位: {no.original_position_limit:.0%}")
            print(f"  覆盖后仓位: {no.overridden_position_limit}")
            print(f"  原因: {no.reason}")
            print(f"  验证事件: {no.validated_events}")
        else:
            print("  无消息面覆盖事件")

        # 时间一致性
        _print_section("时间一致性校验")
        if unified.temporal_inconsistencies:
            for t in unified.temporal_inconsistencies:
                print(f"\n  [{t.severity}] {t.type}")
                print(f"    {t.description}")
                print(f"    动作: {t.action}")
        else:
            print("  无时间一致性异常（首日运行或全部通过）")

        # 风险与机会
        _print_section("关键风险")
        for r in unified.key_risks:
            print(f"  ⚠ {r}")

        _print_section("关键机会")
        for o in unified.key_opportunities:
            print(f"  ★ {o}")

        # 新概念建议
        _print_section("新概念建议")
        if unified.concept_suggestions:
            for c in unified.concept_suggestions:
                print(f"  • {c.get('concept_name', '?')}: {c.get('evidence', '?')}")
        else:
            print("  无新概念建议")

        # Reasoning
        _print_section("综合推理")
        print(f"  {unified.reasoning or '无'}")

        # 数据质量
        _print_section("数据质量")
        dq = unified.data_quality
        print(f"  可用字段: {dq.available_fields}")
        print(f"  缺失字段: {dq.missing_fields}")
        print(f"  失败字段: {dq.fetch_failed_fields}")

        # 记录潜在问题
        if unified.status == "manual_review_required":
            issues.append("状态: manual_review_required")
        if unified.overall_confidence < 0.3:
            issues.append(f"置信度过低: {unified.overall_confidence:.2f}")
        if dq.fetch_failed_fields:
            issues.append(f"数据获取失败: {dq.fetch_failed_fields}")

    # 最终总结
    _print_section("测试总结")
    print(f"\n  总耗时: {elapsed:.1f} 秒")
    print(f"  测试日期: {date.today().isoformat()}")

    if issues:
        print(f"\n  ⚠ 发现 {len(issues)} 个问题:")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("\n  ✅ 端到端测试通过，无异常")

    # 保存结果到 JSON
    if unified:
        output_path = Path("data/phase1_e2e_result.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result_dict = unified.model_dump()
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n  结果已保存: {output_path}")

    print()
    return unified


if __name__ == "__main__":
    asyncio.run(run_phase1_e2e())
