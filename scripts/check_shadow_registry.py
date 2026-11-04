#!/usr/bin/env python3
"""影子模式登记表检查器。

用法：
    python scripts/check_shadow_registry.py
    python scripts/check_shadow_registry.py --fail-on-expired

退出码：
    0 = 结构通过；到期项仅提示
    1 = 结构错误（未登记/缺字段/可回测 gate 无 waiver）
    2 = --fail-on-expired 且存在已到期 gate
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.got.shadow_registry import (  # noqa: E402
    SHADOW_REGISTRY,
    discover_shadow_keys,
    expired_gates,
    validate_registry,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fail-on-expired",
        action="store_true",
        help="存在已到期 gate 时退出 2（默认只提示）",
    )
    args = parser.parse_args()

    errors = validate_registry()
    discovered = discover_shadow_keys()
    unknown = sorted(discovered - set(SHADOW_REGISTRY))
    stale = sorted(set(SHADOW_REGISTRY) - discovered)

    expired = expired_gates(SHADOW_REGISTRY)

    kind_counts = Counter(rec.kind for rec in SHADOW_REGISTRY.values())
    status_counts = Counter(rec.status for rec in SHADOW_REGISTRY.values())

    print(f"[shadow-registry] entries={len(SHADOW_REGISTRY)} discovered={len(discovered)}")
    print(f"[shadow-registry] kind={dict(kind_counts)} status={dict(status_counts)}")

    if unknown:
        print("[shadow-registry] 未登记: " + ", ".join(unknown), file=sys.stderr)
    if stale:
        print("[shadow-registry] 登记表多余: " + ", ".join(stale), file=sys.stderr)
    if errors:
        print("[shadow-registry] 结构性错误:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
    if expired:
        print("[shadow-registry] 已到期 gate（默认 retire，续期须写 waiver）:")
        for key, expiry in expired:
            rec = SHADOW_REGISTRY[key]
            print(f"  - {key} expiry={expiry} status={rec.status} waiver={rec.waiver or '<none>'}")

    if errors or unknown or stale:
        return 1
    if args.fail_on_expired and expired:
        return 2
    print("[shadow-registry] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
