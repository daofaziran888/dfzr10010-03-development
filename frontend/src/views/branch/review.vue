<template>
  <section class="page" data-module="branch-review">
    <header class="page-head">
      <div>
        <h2>分支合并待复核</h2>
        <p class="page-desc">
          按合并批次展示冲突字段、唯一可恢复的目标快照与血缘路径；同一快照完成确认后，合并审计台账保持完整。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="goBranch">返回分支列表</button>
        <button class="btn" type="button" :disabled="loading" @click="reload">{{ loading ? '刷新中…' : '刷新' }}</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">待复核批次</span>
        <strong class="stat-value">{{ pendingCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已复核批次</span>
        <strong class="stat-value">{{ confirmedCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">可恢复快照</span>
        <strong class="stat-value">{{ items.length }}</strong>
      </article>
    </div>

    <div class="filter-bar">
      <label class="filter-item">
        <span>确认状态</span>
        <select v-model="statusFilter" @change="reload">
          <option value="">全部</option>
          <option value="待复核">待复核</option>
          <option value="已确认">已确认</option>
        </select>
      </label>
      <button class="btn ghost" type="button" @click="statusFilter = ''; reload()">重置条件</button>
    </div>

    <p v-if="!items.length" class="empty-state panel">暂无待复核的合并批次，请先在分支列表完成合并并生成目标快照。</p>

    <article
      v-for="item in items"
      :key="item['合并批次']"
      class="panel review-card"
      :data-batch="item['合并批次']"
    >
      <header class="panel-head">
        <div>
          <h3>合并批次 {{ item['合并批次'] }} · {{ item['表名称'] }}</h3>
          <p class="page-desc">
            {{ item['源分支'] }} → {{ item['目标分支'] }} · 父快照 {{ item['父快照'] || '—' }} ·
            <span :class="item['确认状态'] === '已确认' ? 'ok-text' : 'warn-text'">{{ item['确认状态'] }}</span>
          </p>
        </div>
        <div class="row-actions">
          <button
            class="btn primary"
            type="button"
            :disabled="item['确认状态'] === '已确认'"
            @click="confirm(item)"
          >
            {{ item['确认状态'] === '已确认' ? '已确认' : '确认同一快照' }}
          </button>
        </div>
      </header>

      <div class="detail-grid">
        <span>冲突字段：{{ item['冲突字段'] }}</span>
        <span>冲突处理：{{ item['冲突处理'] }}</span>
        <span>目标快照：{{ item['目标快照']?.['快照编号'] }}</span>
        <span>血缘记录：{{ item['血缘记录']?.['记录编号'] }}</span>
        <span>合并时间：{{ item['合并时间'] }}</span>
        <span>血缘路径：{{ item['血缘路径'] }}</span>
      </div>

      <table class="data-table">
        <thead>
          <tr>
            <th>字段名</th>
            <th>源分支值</th>
            <th>目标分支值</th>
            <th>源分支更新时间</th>
            <th>目标分支更新时间</th>
            <th>冲突</th>
            <th>采用值</th>
            <th>采用来源</th>
            <th>处理依据</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="field in item['字段冲突']" :key="String(field['字段名'])" class="conflict-row">
            <td>{{ field['字段名'] }}</td>
            <td>{{ field['源分支值'] }}</td>
            <td>{{ field['目标分支值'] }}</td>
            <td>{{ field['源分支更新时间'] || '—' }}</td>
            <td>{{ field['目标分支更新时间'] || '—' }}</td>
            <td>{{ field['是否冲突'] ? '冲突' : '一致' }}</td>
            <td>{{ field['采用值'] }}</td>
            <td>{{ field['采用来源'] }}</td>
            <td>{{ field['处理依据'] }}</td>
          </tr>
          <tr v-if="!item['字段冲突']?.length">
            <td colspan="9" class="empty-state">该批次没有字段冲突，目标快照已按目标分支记录生成</td>
          </tr>
        </tbody>
      </table>

      <div class="snapshot-box">
        <h4>目标数据表快照 {{ item['目标快照']?.['快照编号'] }}</h4>
        <div class="detail-grid">
          <span>表名称：{{ item['目标快照']?.['表名称'] }}</span>
          <span>快照版本：{{ item['目标快照']?.['快照版本'] }}</span>
          <span>创建方式：{{ item['目标快照']?.['创建方式'] }}</span>
          <span>快照状态：{{ item['目标快照']?.['快照状态'] }}</span>
          <span>父快照：{{ item['目标快照']?.['父快照'] || '—' }}</span>
          <span>确认状态：{{ item['目标快照']?.['确认状态'] }}</span>
        </div>
        <table class="data-table">
          <thead>
            <tr><th>字段名</th><th>快照值</th></tr>
          </thead>
          <tbody>
            <tr v-for="(value, name) in item['目标快照']?.['快照内容'] ?? {}" :key="String(name)">
              <td>{{ name }}</td>
              <td>{{ value }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="snapshot-box">
        <h4>血缘明细</h4>
        <div class="detail-grid">
          <span>记录编号：{{ item['血缘记录']?.['记录编号'] }}</span>
          <span>操作批次：{{ item['血缘记录']?.['操作批次'] }}</span>
          <span>快照编号：{{ item['血缘记录']?.['快照编号'] }}</span>
          <span>父快照：{{ item['血缘记录']?.['父快照'] || '—' }}</span>
          <span>血缘路径：{{ item['血缘记录']?.['血缘路径'] }}</span>
        </div>
        <table class="data-table">
          <thead>
            <tr><th>源字段</th><th>目标字段</th><th>源分支值</th><th>目标分支值</th><th>采用值</th><th>采用来源</th></tr>
          </thead>
          <tbody>
            <tr v-for="map in item['血缘记录']?.['字段映射明细'] ?? []" :key="String(map['源字段'])">
              <td>{{ map['源字段'] }}</td>
              <td>{{ map['目标字段'] }}</td>
              <td>{{ map['源分支值'] }}</td>
              <td>{{ map['目标分支值'] }}</td>
              <td>{{ map['采用值'] }}</td>
              <td>{{ map['采用来源'] }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="snapshot-box">
        <h4>合并校验与联动结果</h4>
        <div class="detail-grid">
          <span>校验结果：{{ item['校验结果'] }}</span>
          <span>处理结果：{{ item['处理结果'] }}</span>
        </div>
        <ul class="linkage-list">
          <li v-for="link in item['联动处理'] ?? []" :key="String(link['链路'])">
            {{ link['链路'] }} · {{ link['对象'] }} · {{ link['结果'] }}
          </li>
        </ul>
      </div>
    </article>

    <footer class="page-foot">
      <span>共 {{ items.length }} 个合并批次</span>
      <span v-if="notice" class="ok-text">{{ notice }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { request } from '@/api/client'

type AnyRecord = Record<string, any>

const ENDPOINT = '/api/branch'
const route = useRoute()
const router = useRouter()

const items = ref<AnyRecord[]>([])
const statusFilter = ref('')
const loading = ref(false)
const errorMessage = ref('')
const notice = ref('')

const pendingCount = computed(() => items.value.filter((item) => item['确认状态'] === '待复核').length)
const confirmedCount = computed(() => items.value.filter((item) => item['确认状态'] === '已确认').length)

function goBranch() {
  void router.push('/branch')
}

async function reload() {
  loading.value = true
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (statusFilter.value) {
    params.set('status', statusFilter.value)
  }
  try {
    const response = await request(`${ENDPOINT}/reviews?${params.toString()}`)
    if (!response.ok) {
      throw new Error('待复核批次读取失败')
    }
    const payload = await response.json()
    items.value = payload.items ?? []
    if (route.query.batch) {
      notice.value = `已定位合并批次 ${route.query.batch}`
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '待复核批次读取失败'
  } finally {
    loading.value = false
  }
}

async function confirm(item: AnyRecord) {
  errorMessage.value = ''
  notice.value = ''
  try {
    const response = await request(`${ENDPOINT}/reviews/${encodeURIComponent(item['合并批次'])}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ values: { resolution: item['冲突处理'] } }),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message || payload.detail || '待复核确认未生效')
    }
    notice.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '待复核确认失败'
  }
}

onMounted(() => {
  // 从分支列表跳转过来时带上批次号，便于直接定位同一快照。
  if (typeof route.query.status === 'string') {
    statusFilter.value = route.query.status
  }
  void reload()
})
</script>
