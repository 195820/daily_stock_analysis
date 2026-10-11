# -*- coding: utf-8 -*-
"""OPEN-D10 · direction 字段语义净化迁移脚本（一次性）。

来源：外网对标实施计划 §2（项二）§2.3。

背景：`direction` 曾混用两套正交语义——价格方向（positive/negative/binary）与事件
类型（drift/fade/binary）。`_check_direction` / `_determine_pead_direction` 只认前三值，
drift/fade 行恒判 False/neutral，导致约 35% 兑现样本误判、污染 P6 direction_accuracy
全部下游统计。

修法①（修源头）：direction 统一为价格语义，事件类型只保留在 category_tag。
- catalyst_events.direction：存量 drift/fade → binary（category_tag 不动，已含类型）
- catalyst_realizations.direction：同上，并对命中行**翻转即重算** drift_direction_correct /
  pead_direction（用同一份 `_check_direction` / `_determine_pead_direction`，禁第二套口径）。

安全约束：默认 dry-run 只预览，须显式 --apply 才写库；写库前打印逐表计数。

用法（--apply 时翻转与重算绑定，幂等可二次跑修复/重算）：
    python scripts/migrate_direction_semantics.py            # 预览（不写）
    python scripts/migrate_direction_semantics.py --apply     # 翻转 direction + 重算正确性
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, or_  # noqa: E402

from src.storage import (  # noqa: E402
    CatalystEventRecord,
    CatalystRealizationRecord,
    DatabaseManager,
)

logger = logging.getLogger("migrate_direction_semantics")

# 事件类型语义（污染值）→ 价格语义归一目标
POLLUTED = ("drift", "fade")
NORMALIZED = "binary"


def _migrate_events(session, apply: bool) -> int:
    """catalyst_events：direction drift/fade → binary。category_tag 保留原类型不动。"""
    q = session.query(CatalystEventRecord).filter(
        CatalystEventRecord.direction.in_(POLLUTED)
    )
    rows = q.all()
    by_dir = {}
    for r in rows:
        by_dir[r.direction] = by_dir.get(r.direction, 0) + 1
    print(f"[catalyst_events] 命中污染行 {len(rows)} 分布={by_dir}"
          f"{' → direction=binary' if apply else '（dry-run，未写）'}")
    if apply:
        for r in rows:
            # category_tag 已存类型（drift/fade/binary），迁移不动；若为空则回填原值留痕
            if not (r.category_tag or "").strip():
                r.category_tag = r.direction
            r.direction = NORMALIZED
    return len(rows)


def _migrate_realizations(session, apply: bool) -> int:
    """catalyst_realizations：direction drift/fade → binary，**翻转即重算**正确性字段。

    命中集（幂等 + 可修复先前只翻转未重算的行）：
      direction IN (drift,fade)  OR  (direction='binary' AND category_tag IN (drift,fade))
    —— 前半为待翻转，后半为先前 --apply 未重算遗留的 binary+drift/fade 行（重算修复）。
    apply 时对命中行**始终**用权威 _check_direction/_determine_pead_direction 重算，消除
    「direction=binary 却标未命中」的自相矛盾；重算完全由已存 actual_return/day_* 推导，幂等。
    """
    q = session.query(CatalystRealizationRecord).filter(or_(
        CatalystRealizationRecord.direction.in_(POLLUTED),
        (CatalystRealizationRecord.direction == NORMALIZED)
        & CatalystRealizationRecord.category_tag.in_(POLLUTED),
    ))
    rows = q.all()
    to_flip = sum(1 for r in rows if r.direction in POLLUTED)
    repair = len(rows) - to_flip
    print(f"[catalyst_realizations] 命中行 {len(rows)}（待翻转 {to_flip} / 已翻转待重算修复 {repair}）"
          f"{' → direction=binary + 重算 drift_direction_correct/pead_direction' if apply else '（dry-run，未写）'}")
    if not apply:
        return len(rows)

    # 复用权威判定方法，禁止在脚本内另写一套阈值口径
    from src.got.catalyst_realization import CatalystRealizationTracker
    tracker = CatalystRealizationTracker()

    for r in rows:
        if r.direction in POLLUTED:
            if not (r.category_tag or "").strip():
                r.category_tag = r.direction
            r.direction = NORMALIZED
        # 翻转后（或本已 binary）统一按 binary 语义重算——幂等
        if r.actual_return is not None:
            r.drift_direction_correct = tracker._check_direction(
                r.direction, r.actual_return
            )
        window_returns = {
            "day_1": r.day_1_return,
            "day_3": r.day_3_return,
            "day_5": r.day_5_return,
        }
        if any(v is not None for v in window_returns.values()):
            r.pead_direction = tracker._determine_pead_direction(
                r.direction, window_returns
            )
    return len(rows)


def _validate(session) -> bool:
    """迁移后校验：两张表 direction 取值应仅 ∈ {positive, negative, binary}。"""
    ok = True
    allowed = {"positive", "negative", "binary"}
    for model, name in (
        (CatalystEventRecord, "catalyst_events"),
        (CatalystRealizationRecord, "catalyst_realizations"),
    ):
        distinct = {
            v for (v,) in session.query(model.direction)
            .group_by(model.direction).all() if v is not None
        }
        bad = distinct - allowed
        total = session.query(func.count()).select_from(model).scalar()
        print(f"[校验] {name} 总行={total} direction 取值={sorted(distinct)} "
              f"越界={sorted(bad) if bad else '无'}")
        if bad:
            ok = False
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="OPEN-D10 direction 语义净化迁移")
    parser.add_argument("--apply", action="store_true",
                        help="实际写库（默认 dry-run 仅预览）；apply 时对命中行**翻转即重算**，幂等可二次修复")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    db = DatabaseManager()
    with db.get_session() as session:
        print("=" * 60)
        print(f"OPEN-D10 direction 语义净化迁移  mode={'APPLY' if args.apply else 'DRY-RUN'}")
        print("=" * 60)
        n_ev = _migrate_events(session, apply=args.apply)
        n_re = _migrate_realizations(session, apply=args.apply)
        if args.apply:
            session.commit()
            print(f"[OK] 已提交：events={n_ev} realizations={n_re}")
        else:
            session.rollback()
            print("（dry-run：未改动数据库。加 --apply 执行写库）")

    with db.get_session() as session:
        valid = _validate(session)

    print("=" * 60)
    print("迁移后校验：" + ("[OK] 全部合法" if valid else "[FAIL] 仍有越界取值，请复核"))
    return 0 if (valid or not args.apply) else 1


if __name__ == "__main__":
    raise SystemExit(main())
