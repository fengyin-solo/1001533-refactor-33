"""经费结算业务规则：状态流转、字段校验、金额口径与统计汇总都收在这里。

金额相关（应付/已付/未付、四舍五入、千分位）一律委托 ``app.money``，
列表、动作、统计、对账文件不再有第二套算法。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app import money
from app.store import store

MODULE = "settlement"
REQUIRED_FIELDS = ["结算单号", "关联合同", "结算周期"]
STATUS_ORDER = ["待核算", "待审核", "已付款", "已驳回"]
ACTION_RULES = {"提交审核": "待审核", "确认付款": "已付款", "驳回结算": "已驳回"}
NEGATIVE_ACTIONS = ["驳回结算"]

# 列表/明细/对账文件对外展示的统一字段顺序
PRESENT_FIELDS = [
    "id", "结算单号", "关联合同", "结算周期",
    money.PAYABLE_FIELD, money.PAID_FIELD, money.UNPAID_FIELD,
    "审核人员", "付款日期", "结算状态", money.NOTE_FIELD,
]

FILTER_FIELDS = {"code": "结算单号", "contract": "关联合同", "period": "结算周期"}


class SettlementService:
    # ---- 取数与筛选 -------------------------------------------------
    def _filtered(self, **filters: Any) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        for param, field in FILTER_FIELDS.items():
            keyword = str(filters.get(param) or "").strip()
            if keyword:
                rows = [row for row in rows if keyword in str(row.get(field, ""))]
        status = str(filters.get("status") or "").strip()
        if status:
            rows = [row for row in rows if str(row.get("status") or "") == status]
        return rows

    def list_entries(
        self,
        *,
        code: str | None = None,
        contract: str | None = None,
        period: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._filtered(code=code, contract=contract, period=period, status=status)
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = [self.present_entry(row) for row in rows[start:start + size]]
        return page_rows, total

    def all_entries(self, **filters: Any) -> list[dict[str, Any]]:
        """统计与对账文件共用的全量取数口径（经过统一序列化）。"""
        return [self.present_entry(row) for row in self._filtered(**filters)]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self.present_entry(entry) if entry is not None else None

    # ---- 统一序列化 -------------------------------------------------
    def present_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """把存仓库的原始单据整理成对外结构。

        金额取数失败不应让整行消失：按 0 兜底并在金额说明里写清原因，
        保证异常单据仍能落到列表与对账明细里。
        """
        try:
            payable = money.payable_amount(entry)
            paid = money.paid_amount(entry)
            unpaid = money.unpaid_amount(entry)
            note = money.money_note(entry)
        except (ArithmeticError, ValueError, TypeError) as exc:  # 取数异常也要落明细
            payable, paid, unpaid = None, money.ZERO, money.ZERO
            note = f"金额取数异常：{exc}，请核对原始登记"

        presented = {
            "id": entry.get("id"),
            "结算状态": entry.get("status"),
            money.PAYABLE_FIELD: self._number(payable),
            money.PAID_FIELD: self._number(money.round_money(paid)),
            money.UNPAID_FIELD: self._number(unpaid),
            money.NOTE_FIELD: note,
            money.FETCH_OK_FIELD: payable is not None,
        }
        for field in ("结算单号", "关联合同", "结算周期", "审核人员", "付款日期"):
            presented[field] = entry.get(field)
        return presented

    @staticmethod
    def _number(value: Decimal | None) -> float | None:
        """对外只给数值（分精度），千分位/小数由统一展示层处理。"""
        return float(value) if value is not None else None

    # ---- 登记与动作 -------------------------------------------------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 应付金额可后补：没填时保留 None，由统一算法给出可读说明
        payable = money.to_decimal(values.get(money.PAYABLE_FIELD))
        entry[money.PAYABLE_FIELD] = float(money.round_money(payable)) if payable is not None else None
        entry[money.PAID_FIELD] = 0.0
        entry["审核人员"] = values.get("审核人员")
        entry["付款日期"] = None
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self.present_entry(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"结算单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于经费结算可执行范围"

        current = str(entry.get("status") or "")
        if current in money.HISTORICAL_STATUSES:
            return None, f"结算单当前为{current}状态，金额已按原值封存，不能再执行{action}"

        if action == "提交审核":
            if current != "待核算":
                return None, f"结算单当前为「{current}」，只有待核算单据可以提交审核"
        if action in ("确认付款", "驳回结算") and current != "待审核":
            return None, f"结算单当前为「{current}」，需要先提交审核并通过后才能{action}"

        if action == "确认付款":
            payable = money.payable_amount(entry)
            if payable is None:
                return None, "应付金额未填或无法识别，请补录应付金额后再确认付款"
            # 付款只算这一次：按统一口径把已付写成应付、未付封存为 0
            paid = money.round_money(payable)
            entry[money.PAID_FIELD] = float(paid)
            entry[money.UNPAID_FIELD] = 0.0
            entry["付款日期"] = date.today().isoformat()
        elif action == "驳回结算":
            # 驳回时把当前未付按统一口径封存，之后只按原值读取
            entry[money.UNPAID_FIELD] = float(money.unpaid_amount(entry))
            entry["abnormal"] = True

        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target == "待审核"
        return self.present_entry(entry), f"结算单已{action}"

    # ---- 统计 -------------------------------------------------------
    def summarize(self, *, month: str | None = None, **filters: Any) -> dict[str, Any]:
        """统计与列表共用同一份取数与金额算法，保证刷新后对得上。"""
        rows = self.all_entries(**filters)
        month = month or date.today().strftime("%Y-%m")
        pending_review = sum(1 for row in rows if row["结算状态"] == "待审核")
        rejected = sum(1 for row in rows if row["结算状态"] == "已驳回")
        monthly_paid = Decimal("0")
        for row in rows:
            if row["结算状态"] == "已付款" and str(row.get("付款日期") or "").startswith(month):
                monthly_paid += money.to_decimal(row[money.PAID_FIELD]) or Decimal("0")
        unpaid_total = sum(
            (money.to_decimal(row[money.UNPAID_FIELD]) or Decimal("0") for row in rows),
            Decimal("0"),
        )
        return {
            "month": month,
            "pending_review": pending_review,
            "monthly_paid": float(money.round_money(monthly_paid)),
            "rejected": rejected,
            "unpaid_total": float(money.round_money(unpaid_total)),
        }
