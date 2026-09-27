<template>
  <section class="page" data-module="settlement">
    <header class="page-head">
      <div>
        <h2>经费结算管理</h2>
        <p class="page-desc">维护结算单，围绕结算单号、关联合同、结算周期、应付金额做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记结算单</button>
        <button class="btn" type="button" @click="exportRows">导出对账文件</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.valueText }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>结算单号</span>
        <input v-model="keyword" placeholder="按结算单号检索" />
      </label>
      <label class="filter-item">
        <span>结算状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>金额说明</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-fetch-failed': row._取数失败 }">
          <td v-for="column in columns" :key="column" :class="cellClass(row, column)">
            {{ cellText(row, column) }}
          </td>
          <td class="amount-note" :class="{ 'amount-invalid': row._金额异常 || row._取数失败 }">{{ row['金额说明'] || '' }}</td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <span v-if="!availableActions(row).length" class="muted">历史单据只读</span>
          </td>
        </tr>
        <tr v-if="!rows.length && !errorMessage">
          <td :colspan="columns.length + 2" class="empty-state">暂无符合条件的结算单</td>
        </tr>
        <tr v-if="errorMessage">
          <td :colspan="columns.length + 2" class="empty-state error-text">
            {{ errorMessage }}
            <button class="btn ghost" type="button" @click="reload">重试取数</button>
          </td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条经费结算记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'
import { formatAmount } from '@/utils/money'

type AmountView = {
  id: number
  status: string
  '结算单号'?: string
  '关联合同'?: string
  '结算周期'?: string
  '应付金额'?: string | number | null
  '已付金额'?: string | number | null
  '审核人员'?: string | null
  '付款日期'?: string | null
  '结算状态'?: string
  '应付金额(元)'?: string
  '已付金额(元)'?: string
  '未付金额(元)'?: string
  '金额说明'?: string
  _零金额?: boolean
  _取数失败?: boolean
  _金额异常?: boolean
  [key: string]: string | number | boolean | null | undefined
}

type StatCard = { label: string; value: number; valueText: string }

const ENDPOINT = '/api/settlement'
// 展示列：未付金额是统一算法派生值，结余与它同一口径，不再单独换算。
const columns = ['结算单号', '关联合同', '结算周期', '应付金额(元)', '已付金额(元)', '未付金额(元)', '审核人员', '付款日期', '结算状态']
const rawColumns = ['结算单号', '关联合同', '结算周期', '审核人员', '付款日期', '结算状态']
const amountTextColumns: Record<string, string> = {
  '应付金额(元)': '应付金额(元)',
  '已付金额(元)': '已付金额(元)',
  '未付金额(元)': '未付金额(元)',
}
const actionsByStatus: Record<string, string[]> = {
  待核算: ['提交审核', '驳回结算'],
  待审核: ['确认付款', '驳回结算'],
}
const statuses = ['待核算', '待审核', '已付款', '已驳回']

const rows = ref<AmountView[]>([])
const total = ref(0)
const stats = ref<StatCard[]>([
  { label: '待审核结算', value: 0, valueText: '0' },
  { label: '本月付款金额', value: 0, valueText: '0.00 元' },
  { label: '未付金额合计', value: 0, valueText: '0.00 元' },
  { label: '已驳回单据', value: 0, valueText: '0' },
])
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')

function buildQuery(withStatus = true): string {
  const params: Record<string, string> = {}
  const word = keyword.value.trim()
  if (word) params.keyword = word
  if (withStatus && statusFilter.value) params.status = statusFilter.value
  return new URLSearchParams(params).toString()
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function cellText(row: AmountView, column: string): string {
  if (column in amountTextColumns) {
    // 优先用后端统一文本；万一字段缺失，用分整数兜底，仍是同一个 formatAmount。
    const fenKey = `_${column.startsWith('应付') ? '应付' : column.startsWith('已付') ? '已付' : '未付'}分`
    const text = row[amountTextColumns[column]]
    if (typeof text === 'string') return text
    const fen = row[fenKey]
    return formatAmount(typeof fen === 'number' ? fen : null)
  }
  const value = rawColumns.includes(column) ? row[column] : undefined
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function cellClass(row: AmountView, column: string): Record<string, boolean> {
  if (!(column in amountTextColumns)) return {}
  return {
    'amount-zero': Boolean(row._零金额) && column === '应付金额(元)',
    'amount-invalid': (Boolean(row._金额异常) && column !== '已付金额(元)') || Boolean(row._取数失败),
  }
}

function availableActions(row: AmountView): string[] {
  return actionsByStatus[row.status] ?? []
}

function openCreate() {
  errorMessage.value = '结算单登记入口尚未接入审批流'
}

async function runAction(action: string, row: AmountView) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = (await response.json().catch(() => null)) as { ok?: boolean; message?: string } | null
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '经费结算动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '经费结算操作失败'
  }
}

async function loadStats() {
  // 统计与列表带同一套过滤条件，返回后数量、状态、未付金额直接对得上。
  const response = await request(`${ENDPOINT}/stats?${buildQuery()}`)
  if (!response.ok) throw new Error('结算统计读取失败')
  const payload = (await response.json()) as { items?: StatCard[] }
  if (Array.isArray(payload.items) && payload.items.length) {
    stats.value = payload.items
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}?${buildQuery()}`)
    if (!response.ok) throw new Error('结算单列表读取失败')
    const payload = (await response.json()) as { items?: AmountView[]; total?: number }
    // 接口失败/空数据不伪造：金额为零、无法解析的单据也照常保留在明细里。
    rows.value = Array.isArray(payload.items) ? payload.items : []
    total.value = payload.total ?? rows.value.length
    // 统计接口单独失败只在页脚提示，不影响已经取到的明细。
    try {
      await loadStats()
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '结算统计读取失败'
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '经费结算列表读取失败'
  }
}

async function exportRows() {
  errorMessage.value = ''
  try {
    // 对账文件与列表同源，拿到文件后按要求刷新一遍，未付金额与结算状态仍和统计一致。
    const response = await request(`${ENDPOINT}/export`)
    if (!response.ok) throw new Error('对账文件生成失败')
    const payload = (await response.json()) as { items?: AmountView[] }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = '经费结算对账文件.json'
    link.click()
    URL.revokeObjectURL(url)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '对账文件导出失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.filter-item select {
  padding: 6px 8px;
  border: 1px solid var(--border, #d0d5dd);
  border-radius: 6px;
  background: #fff;
}

.amount-zero {
  color: var(--muted, #64748b);
}

.amount-invalid {
  color: #b42318;
}

.amount-note {
  font-size: 12px;
  color: var(--muted, #64748b);
  max-width: 220px;
}

.row-fetch-failed {
  background: #fef3f2;
}

.muted {
  color: var(--muted, #64748b);
  font-size: 12px;
}
</style>
