<template>
  <section class="page" data-module="settlement">
    <header class="page-head">
      <div>
        <h2>经费结算管理</h2>
        <p class="page-desc">维护结算单，围绕结算单号、关联合同、结算周期、应付金额做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记结算单</button>
        <button class="btn" type="button" @click="exportRows">导出经费结算清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field.key" class="filter-item">
        <span>{{ field.label }}</span>
        <input v-model="filters[field.key]" :placeholder="`按${field.label}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column.key">{{ column.label }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column.key">
            <template v-if="column.money">{{ formatMoney(asMoney(row[column.key])) }}</template>
            <template v-else>{{ displayText(row[column.key]) }}</template>
            <div v-if="column.key === '应付金额' && row.金额说明" class="cell-note">{{ row.金额说明 }}</div>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无经费结算数据，可先登记结算单</td>
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
import { formatMoney, type Money } from '@/utils/money'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/settlement'
const columns = [
  { key: '结算单号', label: '结算单号' },
  { key: '关联合同', label: '关联合同' },
  { key: '结算周期', label: '结算周期' },
  { key: '应付金额', label: '应付金额', money: true },
  { key: '已付金额', label: '已付金额', money: true },
  { key: '未付金额', label: '未付金额', money: true },
  { key: '审核人员', label: '审核人员' },
  { key: '付款日期', label: '付款日期' },
  { key: '结算状态', label: '结算状态' },
]
const actions = ['提交审核', '确认付款', '驳回结算']

const filterFields = [
  { key: 'code', label: '结算单号' },
  { key: 'contract', label: '关联合同' },
  { key: 'period', label: '结算周期' },
]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({ code: '', contract: '', period: '', status: '' })
const stats = ref<{ label: string; value: string | number }[]>([
  { label: '待审核结算', value: 0 },
  { label: '本月付款金额', value: formatMoney(0) },
  { label: '未付金额合计', value: formatMoney(0) },
  { label: '已驳回单据', value: 0 },
])

interface SettlementStats {
  month: string
  pending_review: number
  monthly_paid: number | null
  unpaid_total: number | null
  rejected: number
}

function asMoney(value: string | number | null | undefined): Money {
  if (value === null || value === undefined || value === '') return null
  const numeric = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function displayText(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function queryString() {
  const params = new URLSearchParams()
  for (const field of filterFields) {
    const value = filters.value[field.key]?.trim()
    if (value) params.set(field.key, value)
  }
  const status = filters.value.status?.trim()
  if (status) params.set('status', status)
  const query = params.toString()
  return query ? `?${query}` : ''
}

function resetFilters() {
  filters.value = { code: '', contract: '', period: '', status: '' }
  void reload()
}

function exportRows() {
  // 下载对账文件后立刻按当前筛选刷新，列表未付金额/状态与统计继续对得上
  window.open(`${ENDPOINT}/export${queryString()}`, '_blank')
  void reload()
}

function openCreate() {
  errorMessage.value = '结算单登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = (await response.json().catch(() => null)) as { ok?: boolean; message?: string } | null
    if (!response.ok || !payload || payload.ok === false) {
      throw new Error(payload?.message || '经费结算动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '经费结算操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = queryString()
  const results = await Promise.allSettled([
    request(`${ENDPOINT}${query}`).then(async (response) => {
      if (!response.ok) throw new Error('结算单列表读取失败')
      return (await response.json()) as { items?: Row[]; total?: number }
    }),
    request(`${ENDPOINT}/stats${query}`).then(async (response) => {
      if (!response.ok) throw new Error('结算统计读取失败')
      return (await response.json()) as SettlementStats
    }),
  ])

  const [listResult, statsResult] = results
  if (listResult.status === 'fulfilled') {
    rows.value = listResult.value.items ?? []
    total.value = listResult.value.total ?? rows.value.length
  } else {
    rows.value = []
    total.value = 0
  }
  if (statsResult.status === 'fulfilled') {
    applyStats(statsResult.value)
  }

  const errors = results
    .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
    .map((result) => (result.reason instanceof Error ? result.reason.message : ''))
  errorMessage.value = [...new Set(errors.filter(Boolean))].join('；')
}

function applyStats(data: SettlementStats) {
  stats.value = [
    { label: '待审核结算', value: data.pending_review },
    { label: `本月付款金额（${data.month}）`, value: formatMoney(data.monthly_paid ?? 0) },
    { label: '未付金额合计', value: formatMoney(data.unpaid_total ?? 0) },
    { label: '已驳回单据', value: data.rejected },
  ]
}

onMounted(reload)
</script>
