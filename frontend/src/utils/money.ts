/**
 * 金额统一算法（前端展示口径）。
 *
 * 与后端 app/money.py 保持同一套规则：分单位四舍五入（ROUND_HALF_UP，
 * 不用原生 Math.round 的银行家式误差）、未付 = 应付 - 已付且下限为 0、
 * 已付款/已驳回按接口返回的存档原值读取、千分位与两位小数只有这一种拼法。
 * 权威数值以后端为准，这里只做展示与本地派生，避免页面上再出现第二套算法。
 */

export type Money = number | null

const CENT = 100
export const HISTORICAL_STATUSES = ['已付款', '已驳回'] as const
export type HistoricalStatus = (typeof HISTORICAL_STATUSES)[number]

/** 解析各种写法的金额（去掉千分位、全角逗号、¥、元）；无法识别返回 null。 */
export function parseMoney(value: unknown): number | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value !== 'string') return null
  const text = value
    .trim()
    .replace(/,|，|¥|￥|元/g, '')
    .trim()
  if (!text) return null
  const parsed = Number(text)
  return Number.isFinite(parsed) ? parsed : null
}

/** 唯一的四舍五入口径：精确到分，ROUND_HALF_UP（先转整数分避免浮点误差）。 */
export function roundMoney(value: number): number {
  return Math.round((value + Number.EPSILON) * CENT) / CENT
}

/** 唯一的金额展示写法：两位小数 + 千分位；null 表示取不到数，展示为 —。 */
export function formatMoney(value: Money): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const rounded = roundMoney(value)
  const negative = rounded < 0
  const [integerPart, fractionPart = ''] = Math.abs(rounded).toFixed(2).split('.')
  const grouped = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${negative ? '-' : ''}${grouped}.${fractionPart}`
}

export interface SettlementLike {
  应付金额?: Money | string
  已付金额?: Money | string
  未付金额?: Money | string
  结算状态?: string
}

/** 未付金额的唯一算法；历史单据直接读接口存档的未付金额，不重新换算。 */
export function unpaidAmount(row: SettlementLike): number {
  const status = String(row.结算状态 ?? '')
  if (HISTORICAL_STATUSES.includes(status as HistoricalStatus)) {
    return roundMoney(parseMoney(row.未付金额) ?? 0)
  }
  const payable = parseMoney(row.应付金额)
  if (payable === null) return 0
  const remaining = roundMoney(payable - (parseMoney(row.已付金额) ?? 0))
  return remaining < 0 ? 0 : remaining
}

/** 应付金额没填或无法识别时给出可读说明；正常返回空串。 */
export function payableHint(row: SettlementLike): string {
  const raw = row.应付金额
  if (raw === null || raw === undefined || String(raw).trim() === '') {
    return '应付金额未填，未付金额暂按 0 计，请补录后重新核算'
  }
  if (parseMoney(raw) === null) {
    return `应付金额「${raw}」无法识别，请按数字核对`
  }
  if (roundMoney(parseMoney(raw) as number) === 0) {
    return '应付金额为 0，无需付款'
  }
  return ''
}
