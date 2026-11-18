<template>
  <section class="page" data-module="branch">
    <header class="page-head">
      <div>
        <h2>数据分支合并管理</h2>
        <p class="page-desc">
          读取源分支与目标分支的字段记录，按「已创建 → 合并中 → 已合并」单向推进；冲突处置与目标快照绑定同一合并批次。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记分支记录</button>
        <button class="btn" type="button" @click="exportRows">导出数据分支合并清单</button>
        <button class="btn" type="button" @click="goReview()">前往待复核页</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>分支状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-branch-no="row['分支编号']">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="viewContext(row)">合并依据</button>
            <button
              v-for="action in allowedActions(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button
              v-if="row['合并状态'] === '已完成'"
              class="link"
              type="button"
              @click="goReview(String(row['合并批次'] ?? ''))"
            >
              查看待复核
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据分支合并数据，可先登记分支记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据分支合并记录</span>
      <span v-if="message" class="ok-text">{{ message }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section v-if="context" class="panel" data-panel="merge-context">
      <header class="panel-head">
        <h3>合并依据 · {{ context['分支编号'] }}（{{ context['表名称'] }}）</h3>
        <button class="link" type="button" @click="context = null">收起</button>
      </header>
      <div class="detail-grid">
        <span>源分支：{{ context['源分支'] }}（快照 {{ context['源分支快照'] || '—' }}）</span>
        <span>目标分支：{{ context['目标分支'] }}（快照 {{ context['目标分支快照'] || '—' }}）</span>
        <span>父快照：{{ context['父快照'] || '—' }}</span>
        <span>冲突处理：{{ context['冲突处理'] }}</span>
        <span>合并批次：{{ context['合并批次'] || '尚未创建' }}</span>
        <span>合并状态：{{ context['合并状态'] }}</span>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>字段名</th>
            <th>源分支值</th>
            <th>目标分支值</th>
            <th>源分支更新时间</th>
            <th>目标分支更新时间</th>
            <th>是否冲突</th>
            <th>处理依据</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in context['字段明细']" :key="String(item['字段名'])" :class="{ 'conflict-row': item['是否冲突'] }">
            <td>{{ item['字段名'] }}</td>
            <td>{{ item['源分支值'] }}</td>
            <td>{{ item['目标分支值'] }}</td>
            <td>{{ item['源分支更新时间'] || '—' }}</td>
            <td>{{ item['目标分支更新时间'] || '—' }}</td>
            <td>{{ item['是否冲突'] ? '冲突' : '一致' }}</td>
            <td>{{ item['处理依据'] }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { request } from '@/api/client'

type Row = Record<string, string | number | null | undefined>
type FieldItem = Record<string, string | number | boolean | null | undefined>
type MergeContext = {
  分支编号: string
  表名称: string
  源分支: string
  目标分支: string
  源分支快照?: string
  目标分支快照?: string
  父快照?: string
  冲突处理: string
  合并批次?: string | null
  合并状态: string
  字段明细?: FieldItem[]
}

const ENDPOINT = '/api/branch'
const router = useRouter()
const columns = ['分支编号', '表名称', '分支类型', '源分支', '目标分支', '冲突处理', '合并批次', '合并状态', '分支状态']
const statuses = ['已创建', '合并中', '已合并']
const filterFields = ['分支编号', '表名称', '源分支']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const message = ref('')
const context = ref<MergeContext | null>(null)
const filters = ref<Record<string, string>>({})

const stats = computed(() => [
  { label: '分支记录总数', value: total.value },
  { label: '合并中分支', value: rows.value.filter((row) => row['合并状态'] === '合并中').length },
  { label: '已合并分支', value: rows.value.filter((row) => row['合并状态'] === '已完成').length },
])

function allowedActions(row: Row): string[] {
  if (row['合并状态'] === '合并中') {
    return ['生成目标快照']
  }
  if (row['合并状态'] === '已完成') {
    return []
  }
  return ['执行合并']
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function goReview(batchId = '') {
  const query = batchId ? `?batch=${encodeURIComponent(batchId)}` : ''
  void router.push(`/branch/review${query}`)
}

function openCreate() {
  errorMessage.value = '分支记录登记入口尚未接入审批流'
}

async function viewContext(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/merge-context`)
    if (!response.ok) {
      throw new Error('合并依据读取失败')
    }
    context.value = (await response.json()) as MergeContext
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '合并依据读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  message.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message || payload.detail || '数据分支合并动作未生效，请稍后重试')
    }
    message.value = payload.message
    await reload()
    await viewContext({ ...row, id: payload.entry?.id ?? row.id })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据分支合并操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value) {
      params.set(key, value)
    }
  }
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error('分支记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据分支合并列表读取失败'
  }
}

onMounted(reload)
</script>
