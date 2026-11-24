<template>
  <section class="page" data-module="attribution_handover">
    <header class="page-head">
      <div>
        <h2>归因交班记录</h2>
        <p class="page-desc">
          从差异列表发起的归因核查在这里留痕：领取日期与两个采样编号一一关联；
          已领取的日期不会再次发出。
        </p>
      </div>
      <div class="page-actions">
        <label class="filter-item shift-filter">
          <span>按班组核对</span>
          <input
            v-model="shiftFilter"
            data-testid="shift-filter"
            placeholder="如：日班"
            @change="reload"
          />
        </label>
        <button class="btn" data-testid="handover-reload" type="button" @click="reload">刷新</button>
      </div>
    </header>

    <p v-if="shiftFilter" class="status-legend" data-testid="shift-claimed-summary">
      {{ shiftFilter }}本班领取过的日期：{{ shiftClaimedDates.join('、') || '无' }}
    </p>

    <table class="data-table">
      <thead>
        <tr>
          <th>交班批次</th>
          <th>核查</th>
          <th>指标 / 口径</th>
          <th>范围</th>
          <th>状态</th>
          <th>可领取</th>
          <th>已领取</th>
          <th>说明</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="record in records"
          :key="record.handover_id"
          :data-testid="`handover-record-${record.handover_id}`"
        >
          <td>#{{ record.handover_id }}</td>
          <td>#{{ record.verification_id }}</td>
          <td>{{ record.metric_name }}（v{{ record.version_a }} → v{{ record.version_b }}）</td>
          <td>
            {{ record.scope === 'day' ? `单日 ${record.start_date}` : `整周 ${record.start_date} ~ ${record.end_date}` }}
          </td>
          <td>{{ record.status }}</td>
          <td>{{ record.claimable_dates.join('、') || '—' }}</td>
          <td>{{ record.claimed_dates.join('、') || '—' }}</td>
          <td class="note-cell">{{ record.note }}</td>
        </tr>
        <tr v-if="!records.length">
          <td colspan="8" class="empty-state">尚无归因交班记录，可在「口径差异」页发起核查并交班</td>
        </tr>
      </tbody>
    </table>

    <section v-for="record in records" :key="`detail-${record.handover_id}`" class="panel">
      <h3>批次 #{{ record.handover_id }} 的领取明细</h3>
      <p v-if="record.conclusion" class="page-desc">{{ record.conclusion }}</p>
      <table class="data-table">
        <thead>
          <tr>
            <th>业务日期</th>
            <th>旧口径值</th>
            <th>新口径值</th>
            <th>差值</th>
            <th>旧采样编号</th>
            <th>新采样编号</th>
            <th>领取班组</th>
            <th>领取时间</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in record.items"
            :key="`${record.handover_id}-${item.business_date}`"
            :data-testid="`handover-item-${record.handover_id}-${item.business_date}`"
          >
            <td>{{ item.business_date }}</td>
            <td>{{ item.value_a === null ? '缺' : item.value_a }}</td>
            <td>{{ item.value_b === null ? '缺' : item.value_b }}</td>
            <td>{{ item.diff === null ? '—' : item.diff }}</td>
            <td>{{ item.sample_a_no ?? '—' }}</td>
            <td>{{ item.sample_b_no ?? '—' }}</td>
            <td>{{ item.claimed_shift ?? '—' }}</td>
            <td>{{ item.claimed_at ?? '—' }}</td>
            <td>{{ item.status }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ records.length }} 个交班批次</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { listAttributionHandovers } from '@/api/backend-service'
import type { AttributionHandover } from '@/data/types'

const records = ref<AttributionHandover[]>([])
const shiftFilter = ref('')
const shiftClaimedDates = ref<string[]>([])
const errorMessage = ref('')

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listAttributionHandovers(shiftFilter.value.trim())
    records.value = payload.items
    shiftClaimedDates.value = payload.shiftClaimedDates
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归因交班记录读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.panel { margin-top: 20px; }
.panel h3 { font-size: 14px; margin: 0 0 6px; }
.note-cell { max-width: 320px; font-size: 12px; color: var(--muted); }
.shift-filter input { width: 120px; }
</style>
