# -*- coding: utf-8 -*-
"""P5 审计产物单测：主题账本 shadow 健康检查（只读、纯判定）。"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

_SCRIPT = Path("scripts/check_theme_ledger.py")


def _load():
    spec = importlib.util.spec_from_file_location("_health", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(codes=("880692", "880675"), date_str="2026-10-29") -> dict:
    return {
        "trade_date": date_str,
        "snapshot_version": "abcd1234",
        "observations": [
            {"board_code": c, "board_name": c, "strength": 80.0} for c in codes
        ],
    }


class TestHealth(unittest.TestCase):
    def setUp(self):
        self.h = _load()

    def test_missing_snapshot(self):
        report = self.h.evaluate_checks(
            snapshot=None, expected_date="2026-10-29",
            db_observation_codes=set(), db_duplicate_pks=0,
            unified=None, mode="shadow",
        )
        self.assertEqual(report["status"], "missing")

    def test_pass_when_consistent(self):
        report = self.h.evaluate_checks(
            snapshot=_snapshot(), expected_date="2026-10-29",
            db_observation_codes={"880692", "880675"}, db_duplicate_pks=0,
            unified={"theme_watch_shadow": {"as_of": "2026-10-29"}}, mode="shadow",
        )
        self.assertEqual(report["status"], "pass")

    def test_code_mismatch_fails(self):
        report = self.h.evaluate_checks(
            snapshot=_snapshot(), expected_date="2026-10-29",
            db_observation_codes={"880692"}, db_duplicate_pks=0,
            unified={"theme_watch_shadow": {}}, mode="shadow",
        )
        self.assertEqual(report["status"], "fail")
        names = {c["name"]: c for c in report["checks"]}
        self.assertEqual(names["board_codes_match"]["status"], "fail")

    def test_duplicate_pk_fails(self):
        report = self.h.evaluate_checks(
            snapshot=_snapshot(), expected_date="2026-10-29",
            db_observation_codes={"880692", "880675"}, db_duplicate_pks=1,
            unified={"theme_watch_shadow": {}}, mode="shadow",
        )
        self.assertEqual(report["status"], "fail")

    def test_missing_unified_is_warn_only(self):
        report = self.h.evaluate_checks(
            snapshot=_snapshot(), expected_date="2026-10-29",
            db_observation_codes={"880692", "880675"}, db_duplicate_pks=0,
            unified={}, mode="shadow",
        )
        self.assertEqual(report["status"], "warn")

    def test_date_mismatch_fails(self):
        report = self.h.evaluate_checks(
            snapshot=_snapshot(date_str="2026-10-28"), expected_date="2026-10-29",
            db_observation_codes={"880692", "880675"}, db_duplicate_pks=0,
            unified={}, mode="shadow",
        )
        self.assertEqual(report["status"], "fail")

    def test_collect_with_temp_archive_and_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "got_daily"
            day = root / "20261029"
            day.mkdir(parents=True)
            (day / "theme_ledger.json").write_text(
                json.dumps(_snapshot(), ensure_ascii=False), encoding="utf-8"
            )
            (day / "unified.json").write_text(
                json.dumps({"theme_watch_shadow": {"as_of": "2026-10-29"}}), encoding="utf-8"
            )
            db = Path(tmp) / "t.db"
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE theme_observation (board_code TEXT, trade_date DATE, "
                "PRIMARY KEY (board_code, trade_date))"
            )
            con.executemany(
                "INSERT INTO theme_observation VALUES (?, '2026-10-29')",
                [("880692",), ("880675",)],
            )
            con.execute("CREATE TABLE theme_episode (id TEXT)")
            con.execute("CREATE TABLE theme_setup (id TEXT)")
            con.commit()
            con.close()
            report = self.h.collect(date_str="20261029", root=root, db_path=db, mode="shadow")
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["counts"]["episode"], 0)


if __name__ == "__main__":
    unittest.main()
