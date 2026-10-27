# -*- coding: utf-8 -*-
"""OCR 引擎空闲卸载的行为测试（不加载真实模型）。"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services import portfolio_ocr_import as ocr_module
from src.services.portfolio_ocr_import import PortfolioOcrImportService


class TestOcrIdleUnload(unittest.TestCase):
    def setUp(self):
        self._orig_interval = ocr_module._OCR_WATCHDOG_INTERVAL
        self._orig_idle = ocr_module.OCR_IDLE_UNLOAD_SECONDS

    def tearDown(self):
        ocr_module._OCR_WATCHDOG_INTERVAL = self._orig_interval
        ocr_module.OCR_IDLE_UNLOAD_SECONDS = self._orig_idle

    def test_release_returns_false_when_not_loaded(self):
        svc = PortfolioOcrImportService()
        self.assertFalse(svc.release_engine())
        self.assertIsNone(svc._ocr)

    def test_release_drops_loaded_engine(self):
        svc = PortfolioOcrImportService()
        svc._backend = "rapidocr"
        svc._ocr = object()

        self.assertTrue(svc.release_engine())
        self.assertIsNone(svc._ocr, "卸载后引用必须清空，否则内存不会释放")

    def test_engine_reloads_after_release(self):
        svc = PortfolioOcrImportService()
        svc._backend = "rapidocr"
        created = []

        class _FakeEngine:
            def __init__(self):
                created.append(1)

        svc._ocr = None
        # 替换后端构造，避免加载真实 ONNX 模型
        import types

        fake_mod = types.ModuleType("rapidocr_onnxruntime")
        fake_mod.RapidOCR = _FakeEngine
        sys.modules["rapidocr_onnxruntime"] = fake_mod
        try:
            first = svc._get_ocr()
            svc.release_engine()
            second = svc._get_ocr()
        finally:
            sys.modules.pop("rapidocr_onnxruntime", None)

        self.assertIsNot(first, second)
        self.assertEqual(len(created), 2)

    def test_subprocess_path_is_used_and_boxes_returned(self):
        """Linux 上默认走子进程：父进程不加载模型，直接拿到 boxes。"""
        from unittest import mock

        svc = PortfolioOcrImportService(prefer_subprocess=True)
        fake = mock.Mock(returncode=0, stdout='{"boxes": [{"text": "x", "score": 1}]}', stderr="")
        with mock.patch("subprocess.run", return_value=fake) as run:
            boxes = svc.ocr_boxes("/tmp/a.png")

        self.assertEqual(boxes, [{"text": "x", "score": 1}])
        argv = run.call_args.args[0]
        self.assertIn("src.services.ocr_worker", argv)
        self.assertEqual(argv[-1], "/tmp/a.png")
        self.assertIsNone(svc._ocr, "子进程模式下父进程不应持有模型")

    def test_subprocess_failure_falls_back_inprocess(self):
        from unittest import mock

        svc = PortfolioOcrImportService(prefer_subprocess=True)
        svc._backend = "rapidocr"

        class _Engine:
            def __call__(self, path):
                return [], None

        svc._ocr = _Engine()
        with mock.patch("subprocess.run", side_effect=RuntimeError("boom")):
            with mock.patch.object(svc, "_ocr_boxes_inprocess", return_value=[{"text": "fallback"}]) as inproc:
                boxes = svc.ocr_boxes("/tmp/a.png")

        self.assertEqual(boxes, [{"text": "fallback"}])
        inproc.assert_called_once()

    def test_watchdog_unloads_after_idle_timeout(self):
        ocr_module.OCR_IDLE_UNLOAD_SECONDS = 0  # 先不启动
        svc = PortfolioOcrImportService()
        svc._ocr = object()
        svc._backend = "rapidocr"

        # 直接驱动一次判定逻辑：间隔缩短 + 阈值 0.05s
        ocr_module.OCR_IDLE_UNLOAD_SECONDS = 0.05
        ocr_module._OCR_WATCHDOG_INTERVAL = 0.01
        svc._last_used = time.time() - 1.0
        svc._ensure_watchdog()

        deadline = time.time() + 2.0
        while time.time() < deadline and svc._ocr is not None:
            time.sleep(0.02)

        self.assertIsNone(svc._ocr, "空闲超过阈值应自动卸载引擎")
        svc._watchdog_stop.set()


if __name__ == "__main__":
    unittest.main()
