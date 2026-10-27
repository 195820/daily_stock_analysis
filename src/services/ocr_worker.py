# -*- coding: utf-8 -*-
"""OCR 子进程 worker：加载模型、识别一张图、把框输出成 JSON 后退出。

为什么要单独进程：ONNX Runtime + glibc 会把模型内存留在堆里，进程内 `del` + `gc`
只能归还一部分（实测 132 MB → 94 MB，底噪 15 MB）。子进程退出则全部由内核回收，
常驻的 bot 进程不再背负 ~350 MB 的 OCR 占用。

用法（由 `PortfolioOcrImportService.ocr_boxes` 调用，不直接面向用户）：
    python -m src.services.ocr_worker <image_path>
输出：stdout 一行 JSON `{"boxes": [...]}`；失败时退出码非 0 并写 stderr。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv) -> int:
    if len(argv) < 2:
        print("usage: python -m src.services.ocr_worker <image_path>", file=sys.stderr)
        return 2

    image_path = argv[1]
    if not Path(image_path).exists():
        print(f"image not found: {image_path}", file=sys.stderr)
        return 2

    from src.services.portfolio_ocr_import import PortfolioOcrImportService

    service = PortfolioOcrImportService(prefer_subprocess=False)
    try:
        boxes = service.ocr_boxes(image_path)
    except Exception as exc:  # 显式传回父进程，便于父进程降级/报错
        print(f"ocr failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        service.release_engine()

    json.dump({"boxes": boxes}, sys.stdout, ensure_ascii=False)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
