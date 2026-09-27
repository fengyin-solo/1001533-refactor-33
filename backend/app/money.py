"""金额统一算法：列表展示、动作计算、统计汇总、对账文件都只走这里。

历史上未付/已付/对账结余各写一套：浮点直接相减、各自 round、各自拼千分位，
同一笔单子在不同入口能算出不同结余，历史单据还会被四舍五入成负数。本模块把
口径收敛为：

* 金额一律用 ``Decimal`` 计算，分单位（0.01）做四舍五入（ROUND_HALF_UP）；
* 未付 = 应付 - 已付，结果下限钳到 0，绝不出现负结余；
* 已付款 / 已驳回的历史单据按存档原值读取，不再重新换算；
* 千分位 + 两位小数只有 ``format_money`` 一种拼法。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

PAYABLE_FIELD = "应付金额"
PAID_FIELD = "已付金额"
UNPAID_FIELD = "未付金额"
NOTE_FIELD = "金额说明"
FETCH_OK_FIELD = "金额取数正常"

# 进入这两个状态后金额即成为历史事实，只允许按原值读取
HISTORICAL_STATUSES = ("已付款", "已驳回")


def to_decimal(value: Any) -> Decimal | None:
    """把接口、种子、对账单里各种写法的金额解析成 Decimal；无法识别返回 None。

    去掉千分位逗号与全角逗号、人民币符号与空白后再解析，
    空串、None、非数字都视为"取不到数"，交给上层落说明。
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool 是 int 的子类，金额语义下不接受
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        return None
    for token in (",", "，", "¥", "￥", "元"):
        text = text.replace(token, "")
    text = text.strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def round_money(value: Decimal) -> Decimal:
    """唯一的四舍五入口径：精确到分，ROUND_HALF_UP。"""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def payable_amount(entry: dict[str, Any]) -> Decimal | None:
    return to_decimal(entry.get(PAYABLE_FIELD))


def paid_amount(entry: dict[str, Any]) -> Decimal:
    """已付金额：取不到数按 0 处理（历史单据可能没登记付款）。"""
    return to_decimal(entry.get(PAID_FIELD)) or ZERO


def is_historical(entry: dict[str, Any]) -> bool:
    return str(entry.get("status") or "") in HISTORICAL_STATUSES


def unpaid_amount(entry: dict[str, Any]) -> Decimal:
    """未付金额的唯一算法。

    * 已付款 / 已驳回的历史单据：读存档的 ``未付金额``，没有存档值时记 0，
      绝不用现在的应付/已付重新换算，避免被四舍五入翻成负数；
    * 其余单据：应付 - 已付，四舍五入到分，再钳到 0；
    * 应付金额缺失：未付按 0 展示，具体原因由 :func:`money_note` 说明。
    """
    if is_historical(entry):
        frozen = to_decimal(entry.get(UNPAID_FIELD))
        return round_money(frozen) if frozen is not None else ZERO
    payable = payable_amount(entry)
    if payable is None:
        return ZERO
    remaining = round_money(payable - paid_amount(entry))
    if remaining < ZERO:
        remaining = ZERO
    return remaining


def money_note(entry: dict[str, Any]) -> str:
    """给出该单据金额口径上的可读说明；一切正常时返回空串。"""
    raw_payable = entry.get(PAYABLE_FIELD)
    if raw_payable is None or not str(raw_payable).strip():
        return "应付金额未填，未付金额暂按 0 计，请补录后重新核算"
    if payable_amount(entry) is None:
        return f"应付金额「{raw_payable}」无法识别，请按数字核对"
    payable = payable_amount(entry)
    assert payable is not None
    if payable == 0:
        return "应付金额为 0，无需付款"
    raw_paid = entry.get(PAID_FIELD)
    if raw_paid is not None and str(raw_paid).strip() and to_decimal(raw_paid) is None:
        return f"已付金额「{raw_paid}」无法识别，暂按 0 计入未付"
    return ""


def format_money(value: Decimal | None) -> str:
    """唯一的金额展示写法：两位小数 + 千分位。"""
    if value is None:
        return "—"
    quantized = value if value.as_tuple().exponent == -2 else round_money(value)
    # Decimal 元组会省略前导零，直接用量化后的定点字符串切分更稳妥
    text = str(round_money(quantized))
    if text.startswith("-"):
        head, text = "-", text[1:]
    else:
        head = ""
    integer, _, fraction = text.partition(".")
    grouped = ""
    while integer:
        grouped = integer[-3:] + ("," + grouped if grouped else "")
        integer = integer[:-3]
    return f"{head}{grouped or '0'}.{fraction or '00'}"
