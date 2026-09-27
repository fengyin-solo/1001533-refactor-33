/**
 * 统一金额口径（前端展示层）：
 * 计算一律以后端 `/api/settlement` 返回的 *_分 / *(元) 字段为准，前端不再各写一套。
 * 这里仅保留两个函数：解析（极少数需要本地兜底时用）与展示（两位小数 + 千分位）。
 * 规则与后端 app/amounts.py 保持一致：四舍五入到分，未付/结余不为负。
 */

/** 把任意金额值解析成「分」；无法识别返回 null，绝不静默按 0 处理。 */
export function parseAmountToFen(value: unknown): number | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'boolean') return null
  if (typeof value === 'number') return Math.round(value * 100)
  const text = String(value)
    .replace(/[,，\s　]/g, '')
    .replace(/[¥￥元]/g, '')
    .trim()
  if (!text) return null
  const yuan = Number(text)
  return Number.isFinite(yuan) ? Math.round(yuan * 100) : null
}

/** 唯一的金额展示写法：两位小数 + 千分位；无值显示 —（说明由金额说明列给出）。 */
export function formatAmount(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const fen = Math.round(value)
  const sign = fen < 0 ? '-' : ''
  const yuan = Math.abs(fen) / 100
  return `${sign}${yuan.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
