"""经费结算统一金额口径的回归测试。

运行：cd backend && .venv/bin/python -m unittest app.tests.test_settlement -v
"""
from __future__ import annotations

import asyncio
import unittest
from decimal import Decimal

from app import money
from app.routers.settlement import export_entries
from app.services.settlement import MODULE, SettlementService
from app.store import store


def response_text(response) -> str:
    async def collect() -> bytes:
        return b"".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(collect()).decode("utf-8")


class MoneyAlgorithmTests(unittest.TestCase):
    def test_parse_variants(self):
        self.assertEqual(money.to_decimal("2,450.335"), Decimal("2450.335"))
        self.assertEqual(money.to_decimal("￥1,225.17元"), Decimal("1225.17"))
        self.assertIsNone(money.to_decimal("叁仟元整"))
        self.assertIsNone(money.to_decimal(""))
        self.assertIsNone(money.to_decimal(None))

    def test_round_half_up_to_cent(self):
        self.assertEqual(money.round_money(Decimal("2450.335")), Decimal("2450.34"))
        self.assertEqual(money.round_money(Decimal("0.005")), Decimal("0.01"))

    def test_unpaid_round_and_clamp(self):
        row = {"status": "待核算", "应付金额": "2,450.335", "已付金额": "1,225.17"}
        self.assertEqual(money.unpaid_amount(row), Decimal("1225.17"))
        overpaid = {"status": "待核算", "应付金额": 100, "已付金额": 120}
        self.assertEqual(money.unpaid_amount(overpaid), Decimal("0.00"))

    def test_historical_rows_read_frozen_value(self):
        paid = {"status": "已付款", "应付金额": 100, "已付金额": 120, "未付金额": 0}
        self.assertEqual(money.unpaid_amount(paid), Decimal("0.00"))
        rejected = {"status": "已驳回", "应付金额": 4300, "已付金额": 1000, "未付金额": 3300}
        self.assertEqual(money.unpaid_amount(rejected), Decimal("3300.00"))
        legacy = {"status": "已付款", "应付金额": 100, "已付金额": 100}
        self.assertEqual(money.unpaid_amount(legacy), Decimal("0.00"))

    def test_notes(self):
        self.assertIn("未填", money.money_note({"应付金额": None}))
        self.assertIn("无法识别", money.money_note({"应付金额": "叁仟元整"}))
        self.assertEqual(money.money_note({"应付金额": 0}), "应付金额为 0，无需付款")

    def test_format(self):
        self.assertEqual(money.format_money(Decimal("0")), "0.00")
        self.assertEqual(money.format_money(Decimal("12800")), "12,800.00")
        self.assertEqual(money.format_money(Decimal("-1234567.8")), "-1,234,567.80")
        self.assertEqual(money.format_money(None), "—")


class SettlementServiceTests(unittest.TestCase):
    def setUp(self):
        # store 是单例，按种子重置一次，避免测试间互相污染
        from app import seed as seed_module

        table = store.rows(MODULE)
        table.clear()
        table.extend(dict(row) for row in seed_module.SEED_ROWS[MODULE])
        self.service = SettlementService()

    def test_list_uses_unified_amounts_and_keeps_bad_rows(self):
        items, total = self.service.list_entries(size=50)
        self.assertEqual(total, 9)
        by_id = {row["id"]: row for row in items}
        self.assertEqual(by_id[8]["未付金额"], 1225.17)
        self.assertEqual(by_id[3]["未付金额"], 0.0)
        self.assertEqual(by_id[5]["未付金额"], 3300.0)  # 已驳回按原值
        self.assertIn("未填", by_id[6]["金额说明"])
        self.assertIn("无法识别", by_id[9]["金额说明"])
        self.assertTrue(all(6 <= row["id"] <= 9 for row in items if row["id"] in (6, 7, 8, 9)))

    def test_stats_match_rows(self):
        stats = self.service.summarize(month="2026-09")
        self.assertEqual((stats["pending_review"], stats["rejected"]), (3, 1))
        self.assertEqual(stats["monthly_paid"], 18420.55)
        self.assertAlmostEqual(stats["unpaid_total"], 19975.72, places=2)

    def test_confirm_payment_freezes_zero_unpaid(self):
        entry, _ = self.service.run_action(2, "确认付款")
        self.assertEqual(entry["结算状态"], "已付款")
        self.assertEqual(entry["已付金额"], 8650.55)
        self.assertEqual(entry["未付金额"], 0.0)
        again, message = self.service.run_action(2, "确认付款")
        self.assertIsNone(again)
        self.assertIn("封存", message)

    def test_reject_freezes_current_unpaid_and_keeps_it(self):
        self.service.run_action(1, "提交审核")
        entry, _ = self.service.run_action(1, "驳回结算")
        self.assertEqual(entry["未付金额"], 6800.0)
        # 即使之后原始应付被改动，历史单据仍读存档值，不会变成负数
        store.find(MODULE, 1)["应付金额"] = 0
        self.assertEqual(self.service.get_entry(1)["未付金额"], 6800.0)

    def test_confirm_payment_without_payable_is_blocked(self):
        entry, message = self.service.run_action(6, "确认付款")
        self.assertIsNone(entry)
        self.assertIn("应付金额未填", message)
        entry, message = self.service.run_action(9, "确认付款")
        self.assertIsNone(entry)
        self.assertIn("无法识别", message)

    def test_export_keeps_zero_and_bad_rows_with_summary(self):
        text = response_text(export_entries())
        for code in ("SETT-0006", "SETT-0007", "SETT-0009"):
            self.assertIn(code, text)
        self.assertIn("应付金额未填", text)
        self.assertIn("无需付款", text)
        self.assertIn("未付金额合计", text)


if __name__ == "__main__":
    unittest.main()
