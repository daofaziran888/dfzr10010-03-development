<template>
  <section class="page" data-module="branch-audit">
    <header class="page-head">
      <div>
        <h2>合并审计台账</h2>
        <p class="page-desc">
          每个合并批次一条记录：冲突处置、目标数据表快照、血缘引用与业务链路处理结果统一留痕，刷新后保持完整。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="goReview">前往待复核页</button>
        <button class="btn" type="button" :disabled="loading" @click="reload">{{ loading ? '刷新中…' : '刷新台账' }}</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">合并批次总数</span>
        <strong class="stat-value">{{ items.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已完成批次</span>
        <strong class="stat-value">{{ completedCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待复核批次</span>
        <strong class="stat-value">{{ pendingCount }}</strong>
      </article>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th>合并批次</th>
          <th>分支编号</th>
          <th>表名称</th>
          <th>源分支 → 目标分支</th>
          <th>冲突字段</th>
          <th>校验结果</th>
          <th>目标快照</th>
          <th>血缘记录</th>
          <th>联动处理</th>
          <th>确认状态</th>
          <th>合并时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item['合并批次']" :data-batch="item['合并批次']">
          <td>{{ item['合并批次'] }}</td>
          <td>{{ item['分支编号'] }}</td>
          <td>{{ item['表名称'] }}</td>
          <td>{{ item['源分支'] }} → {{ item['目标分支'] }}</td>
          <td>{{ item['冲突字段'] || '无' }}（{{ item['冲突数量'] }}）</td>
          <td>{{ item['校验结果'] }}</td>
          <td>{{ item['目标快照'] }}</td>
          <td>{{ item['血缘记录'] }}</td>
          <td>
            <span v-for="link in item['联动处理'] ?? []" :key="String(link['链路'])" class="tag">
              {{ link['链路'] }}
            </span>
          </td>
          <td>{{ item['确认状态'] }}</td>
          <td>{{ item['合并时间'] }}</td>
        </tr>
        <tr v-if="!items.length">
          <td colspan="11" class="empty-state">暂无合并批次，请先在分支列表执行合并并生成目标快照</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ items.length }} 条合并批次记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { request } from '@/api/client'

type AnyRecord = Record<string, any>

const ENDPOINT = '/api/branch'
const router = useRouter()
const items = ref<AnyRecord[]>([])
const loading = ref(false)
const errorMessage = ref('')

const completedCount = computed(() => items.value.filter((item) => item['合并状态'] === '已完成').length)
const pendingCount = computed(() => items.value.filter((item) => item['确认状态'] === '待复核').length)

function goReview() {
  void router.push('/branch/review')
}

async function reload() {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/audit`)
    if (!response.ok) {
      throw new Error('合并审计台账读取失败')
    }
    const payload = await response.json()
    items.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '合并审计台账读取失败'
  } finally {
    loading.value = false
  }
}

onMounted(reload)
</script>
