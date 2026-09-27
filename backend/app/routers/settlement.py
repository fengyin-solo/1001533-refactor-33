"""经费结算接口：维护结算单，覆盖提交审核、确认付款、驳回结算等动作。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.settlement import SettlementService

router = APIRouter(prefix="/api/settlement", tags=["经费结算"])

service = SettlementService()

LIST_FIELDS = ["结算单号", "关联合同", "结算周期", "应付金额", "已付金额", "审核人员", "付款日期", "结算状态"]
STATUSES = ["待核算", "待审核", "已付款", "已驳回"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按结算单号检索"),
    status: str | None = Query(default=None, description="待核算、待审核、已付款、已驳回"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按结算单号与状态过滤经费结算列表；没有数据时返回空页，不报错。

    金额列全部由统一金额口径派生，未付金额、结算状态与统计接口天然一致。
    """
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats")
def stats(
    keyword: str | None = Query(default=None, description="与列表一致：按结算单号检索"),
    status: str | None = Query(default=None, description="与列表一致：按状态过滤"),
) -> dict:
    """统计卡片：与列表共用同一份过滤结果和金额算法，返回后直接对账即可。"""
    return service.stats(keyword=keyword, status=status)


@router.get("/export")
def export_entries() -> dict:
    """导出对账文件：金额数值、展示文本、合计都与列表/统计同源同口径。"""
    return service.export_entries()


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
