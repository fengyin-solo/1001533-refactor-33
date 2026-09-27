"""经费结算接口：维护结算单，覆盖提交审核、确认付款、驳回结算等动作。

列表、统计、对账文件都基于 SettlementService 的统一取数与 ``app.money``
的统一金额算法，避免三处各算一套。
"""
from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app import money
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.settlement import PRESENT_FIELDS, SettlementService

router = APIRouter(prefix="/api/settlement", tags=["经费结算"])

service = SettlementService()

# 对账文件明细列（金额列顺序与列表一致，均由 app.money 统一口径产出）
EXPORT_COLUMNS = [
    "结算单号", "关联合同", "结算周期",
    money.PAYABLE_FIELD, money.PAID_FIELD, money.UNPAID_FIELD,
    "审核人员", "付款日期", "结算状态", money.NOTE_FIELD,
]
# 金额列在对账文件中统一两位小数 + 千分位
MONEY_COLUMNS = (money.PAYABLE_FIELD, money.PAID_FIELD, money.UNPAID_FIELD)


def _filters(code: str | None, contract: str | None, period: str | None, status: str | None) -> dict:
    return {"code": code, "contract": contract, "period": period, "status": status}


@router.get("/stats")
def settlement_stats(
    code: str | None = None,
    contract: str | None = None,
    period: str | None = None,
    status: str | None = None,
    month: str | None = Query(default=None, description="本月付款金额统计月份，YYYY-MM"),
) -> dict:
    """统计卡片口径：与当前列表筛选共用同一份取数和同一套金额算法。"""
    return service.summarize(month=month, **_filters(code, contract, period, status))


@router.get("/export")
def export_entries(
    code: str | None = None,
    contract: str | None = None,
    period: str | None = None,
    status: str | None = None,
) -> StreamingResponse:
    """导出对账文件：与列表同筛选口径，末尾附统计汇总行，金额统一两位小数千分位。

    应付/已付取不到数或为 0 的单据也保留在明细里，原因写在金额说明列。
    """
    rows = service.all_entries(**_filters(code, contract, period, status))
    summary = service.summarize(**_filters(code, contract, period, status))

    buffer = io.StringIO()
    # UTF-8 BOM 让 Excel 直接识别中文
    buffer.write("﻿")
    writer = csv.writer(buffer)
    writer.writerow(["经费结算对账文件", f"导出日期：{date.today().isoformat()}"])
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        line = []
        for column in EXPORT_COLUMNS:
            value = row.get(column)
            if column in MONEY_COLUMNS:
                # None 表示没填/取不到数，留空并在金额说明列解释，不能写成 0
                line.append("" if value is None else money.format_money(money.to_decimal(value)))
            else:
                line.append("" if value is None else str(value))
        writer.writerow(line)

    writer.writerow([])
    writer.writerow([
        "合计",
        f"待审核 {summary['pending_review']} 单",
        "",
        "",
        f"本月付款金额：{money.format_money(money.to_decimal(summary['monthly_paid']))}",
        f"未付金额合计：{money.format_money(money.to_decimal(summary['unpaid_total']))}",
        "",
        "",
        f"已驳回 {summary['rejected']} 单",
        f"统计月份：{summary['month']}",
    ])

    data = buffer.getvalue().encode("utf-8")
    filename = f"settlement-reconcile-{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([data]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("", response_model=PageResult[dict])
def list_entries(
    code: str | None = Query(default=None, description="按结算单号检索"),
    contract: str | None = Query(default=None, description="按关联合同检索"),
    period: str | None = Query(default=None, description="按结算周期检索"),
    status: str | None = Query(default=None, description="待核算、待审核、已付款、已驳回"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按结算单号、关联合同、结算周期与状态过滤；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        **_filters(code, contract, period, status), page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条结算单明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"结算单 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条结算单，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="结算单已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条结算单执行提交审核、确认付款、驳回结算；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


# 供可能引用旧常量的模块保持兼容
LIST_FIELDS = PRESENT_FIELDS
