"""统一金额口径：页面展示、动作计算、统计与对账文件都从这里取数。

约定：
- 内部一律以「分」为单位的整数参与运算，避免 0.1 + 0.2 这类浮点误差；
- 展示文本统一保留两位小数并加千分位（1,234.56），小数与千分位只写这一遍；
- 未付金额/结余统一为 max(应付 - 已付, 0)，历史单据（已付款、已驳回）按原
  已付值读取，不重算、不覆盖，四舍五入也不会把结余算成负数；
- 解析不了或未填写的金额不当成 0，统一返回 None 并附可读说明，0 元单据单独标识。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

PAYABLE_FIELD = "应付金额"
PAID_FIELD = "已付金额"
SOURCE_STATE_FIELD = "金额来源状态"
SETTLEMENT_STATUS_FIELD = "结算状态"

# 历史终态：这两类单据的已付金额只按原值读取，任何动作都不再改动。
LOCKED_STATUSES = ("已付款", "已驳回")

# 结算单视图统一新增的字段（原列仍保留原值，供溯源）。
F_PAYABLE_TEXT = "应付金额(元)"
F_PAID_TEXT = "已付金额(元)"
F_UNPAID_TEXT = "未付金额(元)"
F_PAYABLE_FEN = "_应付分"
F_PAID_FEN = "_已付分"
F_UNPAID_FEN = "_未付分"
F_AMOUNT_NOTE = "金额说明"
F_ZERO_AMOUNT = "_零金额"
F_FETCH_FAILED = "_取数失败"
F_AMOUNT_INVALID = "_金额异常"


def parse_amount(value: Any) -> int | None:
    """把任意来源的金额解析成「分」为单位的整数；无法解析时返回 None。

    支持数字、以及带千分位、空格、货币符号、两位小数的文本（"1,234.56 元"）。
    空字符串、None、无法识别的文本一律返回 None，由调用方决定如何提示，
    绝不能悄悄按 0 处理（0 是有效金额，未填写是另一件事）。
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool 是 int 的子类，财务上不应当真值金额
        return None
    if isinstance(value, int):
        return value * 100
    if isinstance(value, float):
        return _decimal_to_fen(Decimal(str(value)))
    text = str(value).strip()
    if not text:
        return None
    cleaned = (
        text.replace(",", "")
        .replace("，", "")
        .replace(" ", "")
        .replace("　", "")
        .replace("¥", "")
        .replace("￥", "")
        .replace("元", "")
    )
    try:
        return _decimal_to_fen(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return None


def _decimal_to_fen(value: Decimal) -> int:
    """元转分：四舍五入（银行柜台常用的 ROUND_HALF_UP，1.005 -> 101）。"""
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fen_to_yuan(value: int) -> float:
    """分转元的数值形式（仅用于数值传输/统计，展示请用 format_amount）。"""
    return (value // 100) + (value % 100) / 100


def format_amount(fen: int | None) -> str:
    """唯一的金额展示写法：两位小数 + 千分位；无值时交给调用方给说明。"""
    if fen is None:
        return "—"
    yuan = abs(fen) / 100
    text = f"{yuan:,.2f}"
    return f"-{text}" if fen < 0 else text


def amount_note(payable: Any, paid: Any, *, payable_fen: int | None, source_state: Any = None) -> str:
    """应付金额没填、解析失败、上游取数失败时给出可读说明（空串表示没有需要提示的事）。"""
    if source_state and "失败" in str(source_state):
        return f"金额接口取数失败（来源状态：{source_state}），该单已落入明细，待补数后复核"
    if payable is None or not str(payable).strip():
        return "应付金额未填写，暂无法核对未付金额"
    if payable_fen is None:
        return f"应付金额「{payable}」无法识别，请填写为数字金额（如 1,234.56）"
    if payable_fen < 0:
        return "应付金额为负数，请先核对原始结算单"
    if payable_fen == 0:
        return "应付金额为 0，本单无需付款，仍保留在明细中备查"
    if paid is not None and str(paid).strip() and parse_amount(paid) is None:
        return f"已付金额「{paid}」无法识别，未付金额暂按未付款处理"
    return ""


def unpaid_fen(payable_fen: int | None, paid_fen: int | None) -> int | None:
    """未付金额/结余的唯一算法：max(应付 - 已付, 0)，绝不出现负结余。"""
    if payable_fen is None:
        return None
    return max(payable_fen - (paid_fen or 0), 0)


def build_settlement_view(entry: dict[str, Any]) -> dict[str, Any]:
    """给一条结算单套上统一金额视图，列表、明细、统计、对账文件共用。

    原始的应付/已付列保留不动（已付款、已驳回的历史值可溯源），计算结果放到
    附加字段里；解析失败、为零的单据照样返回，靠标记和说明落到明细中。
    """
    view = dict(entry)
    payable_raw = entry.get(PAYABLE_FIELD)
    paid_raw = entry.get(PAID_FIELD)

    payable = parse_amount(payable_raw)
    # 历史已付值解析不了时不强行置 0，避免把未付金额虚高。
    paid = parse_amount(paid_raw) if paid_raw is not None and str(paid_raw).strip() else None
    outstanding = unpaid_fen(payable, paid)

    view[F_PAYABLE_FEN] = payable
    view[F_PAID_FEN] = paid
    view[F_UNPAID_FEN] = outstanding
    view[F_PAYABLE_TEXT] = format_amount(payable)
    view[F_PAID_TEXT] = format_amount(paid)
    view[F_UNPAID_TEXT] = format_amount(outstanding)
    view[F_AMOUNT_NOTE] = amount_note(
        payable_raw,
        paid_raw,
        payable_fen=payable,
        source_state=entry.get(SOURCE_STATE_FIELD),
    )
    view[F_FETCH_FAILED] = bool(entry.get(SOURCE_STATE_FIELD) and "失败" in str(entry[SOURCE_STATE_FIELD]))
    view[F_ZERO_AMOUNT] = payable == 0
    payable_bad = payable is None and bool(str(payable_raw or "").strip())
    paid_bad = paid is None and bool(str(paid_raw or "").strip())
    view[F_AMOUNT_INVALID] = payable_bad or paid_bad
    view[SETTLEMENT_STATUS_FIELD] = str(entry.get("status") or "")
    return view
