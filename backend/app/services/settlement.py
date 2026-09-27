"""经费结算业务规则：状态流转、字段校验与筛选口径都收在这里。

金额相关（应付、已付、未付/结余、展示文本、统计合计）一律走 app.amounts，
本模块不再单独算任何金额，保证列表、动作、统计、对账文件同一套口径。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app import amounts
from app.store import store

MODULE = "settlement"
REQUIRED_FIELDS = ["结算单号", "关联合同", "结算周期"]
OPTIONAL_AMOUNT_FIELDS = [amounts.PAYABLE_FIELD, amounts.PAID_FIELD]
STATUS_ORDER = ["待核算", "待审核", "已付款", "已驳回"]
ACTION_RULES = {"提交审核": "待审核", "确认付款": "已付款", "驳回结算": "已驳回"}
NEGATIVE_ACTIONS = ["驳回结算"]
# 每个状态允许继续执行的动作；历史终态不允许再操作。
ALLOWED_ACTIONS: dict[str, list[str]] = {
    "待核算": ["提交审核", "驳回结算"],
    "待审核": ["确认付款", "驳回结算"],
}
_STAT_LABELS = {
    "待审核": "待审核结算",
    "paid_amount": "本月付款金额",
    "unpaid": "未付金额合计",
    "rejected": "已驳回单据",
}


class SettlementService:
    def _filtered_rows(self, *, keyword: str | None, status: str | None) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("结算单号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._filtered_rows(keyword=keyword, status=status)
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = rows[start:start + size]
        return [amounts.build_settlement_view(row) for row in page_rows], total

    def stats(
        self, *, keyword: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        """统计与列表共用同一批过滤结果、同一套金额算法，数量必然对得上。"""
        rows = [amounts.build_settlement_view(row)
                for row in self._filtered_rows(keyword=keyword, status=status)]
        current_month = date.today().strftime("%Y-%m")
        paid_month_fen = 0
        unpaid_fen = 0
        for row in rows:
            if row["status"] == "已付款":
                paid = row[amounts.F_PAID_FEN]
                if paid is not None and str(row.get("付款日期") or "").startswith(current_month):
                    paid_month_fen += paid
            outstanding = row[amounts.F_UNPAID_FEN]
            if outstanding is not None:
                unpaid_fen += outstanding
        return {
            "items": [
                {
                    "label": _STAT_LABELS["待审核"],
                    "value": sum(1 for row in rows if row["status"] == "待审核"),
                    "valueText": str(sum(1 for row in rows if row["status"] == "待审核")),
                },
                {
                    "label": _STAT_LABELS["paid_amount"],
                    "value": amounts.fen_to_yuan(paid_month_fen),
                    "valueText": f"{amounts.format_amount(paid_month_fen)} 元",
                },
                {
                    "label": _STAT_LABELS["unpaid"],
                    "value": amounts.fen_to_yuan(unpaid_fen),
                    "valueText": f"{amounts.format_amount(unpaid_fen)} 元",
                },
                {
                    "label": _STAT_LABELS["rejected"],
                    "value": sum(1 for row in rows if row["status"] == "已驳回"),
                    "valueText": str(sum(1 for row in rows if row["status"] == "已驳回")),
                },
            ],
            "total": len(rows),
        }

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return amounts.build_settlement_view(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in OPTIONAL_AMOUNT_FIELDS:
            if values.get(field) is not None and str(values.get(field)).strip():
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return amounts.build_settlement_view(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"结算单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于经费结算可执行范围"
        current = str(entry.get("status") or "")
        if current in amounts.LOCKED_STATUSES:
            return None, f"结算单已{current}，历史金额按原值留存，不能重复执行{action}"
        if action not in ALLOWED_ACTIONS.get(current, []):
            return None, f"{current}状态的结算单不能执行「{action}」"

        if action == "确认付款":
            source_state = entry.get(amounts.SOURCE_STATE_FIELD)
            if source_state and "失败" in str(source_state):
                return None, f"金额接口取数失败（来源状态：{source_state}），请补数后再确认付款"
            payable = amounts.parse_amount(entry.get(amounts.PAYABLE_FIELD))
            if payable is None:
                raw = entry.get(amounts.PAYABLE_FIELD)
                if raw is None or not str(raw).strip():
                    return None, "应付金额未填写，请先补齐应付金额再确认付款"
                return None, f"应付金额「{raw}」无法识别，请修正为数字金额后再确认付款"
            # 已付金额只在付款这一刻统一写一次：未付清零、结余口径一致。
            entry[amounts.PAID_FIELD] = amounts.fen_to_yuan(payable)
            if not str(entry.get("付款日期") or "").strip():
                entry["付款日期"] = date.today().isoformat()

        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target not in amounts.LOCKED_STATUSES
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return amounts.build_settlement_view(entry), f"结算单已{action}"

    def export_entries(self) -> dict[str, Any]:
        """对账文件：与列表、统计完全同源，金额文本和数值都来自统一口径。"""
        rows = self._filtered_rows(keyword=None, status=None)
        items = [amounts.build_settlement_view(row) for row in rows]
        total_payable = sum(row[amounts.F_PAYABLE_FEN] or 0 for row in items)
        total_paid = sum(row[amounts.F_PAID_FEN] or 0 for row in items)
        total_unpaid = sum(row[amounts.F_UNPAID_FEN] or 0 for row in items)
        return {
            "module": "settlement",
            "total": len(items),
            "currency": "CNY",
            "summary": {
                "应付合计(元)": amounts.format_amount(total_payable),
                "已付合计(元)": amounts.format_amount(total_paid),
                "未付合计(元)": amounts.format_amount(total_unpaid),
            },
            "items": items,
        }
