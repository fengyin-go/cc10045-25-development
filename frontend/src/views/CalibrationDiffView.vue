<template>
  <section class="page" data-module="calibration_diff">
    <header class="page-head">
      <div>
        <h2>口径差异</h2>
        <p class="page-desc">
          按业务日期把同一指标的两个口径快照配对，逐日展示计算得到的差值和没有对应口径的日期；
          只有两版都有已验证采样时才可单独核对。
        </p>
      </div>
      <div class="page-actions">
        <button
          class="btn primary"
          data-testid="diff-create-verification"
          type="button"
          :disabled="!diff || creatingVerification"
          @click="createVerification"
        >
          {{ creatingVerification ? '发起中…' : '从差异列表发起归因核查' }}
        </button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="loadDiff()">
      <label class="filter-item">
        <span>指标</span>
        <select v-model.number="form.metricId" data-testid="diff-metric" @change="syncVersions">
          <option v-for="metric in metrics" :key="metric.metric_id" :value="metric.metric_id">
            {{ metric.metric_name }}
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>旧口径版本</span>
        <select v-model.number="form.versionA" data-testid="diff-version-a">
          <option v-for="version in versions" :key="version" :value="version">v{{ version }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>新口径版本</span>
        <select v-model.number="form.versionB" data-testid="diff-version-b">
          <option v-for="version in versions" :key="version" :value="version">v{{ version }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>开始日期</span>
        <input v-model="form.startDate" data-testid="diff-start" type="date" required />
      </label>
      <label class="filter-item">
        <span>结束日期</span>
        <input v-model="form.endDate" data-testid="diff-end" type="date" required />
      </label>
      <button class="btn" data-testid="diff-load" type="submit">计算差异</button>
    </form>

    <div v-if="diff" class="stat-row">
      <article class="stat-card">
        <span class="stat-label">可单独核对日期</span>
        <strong class="stat-value" data-testid="diff-paired-count">{{ diff.paired_count }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">缺口径日期</span>
        <strong class="stat-value" data-testid="diff-missing-count">{{ diff.missing_count }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">配对日均值（旧/新）</span>
        <strong class="stat-value">{{ formatNumber(diff.mean_a) }} / {{ formatNumber(diff.mean_b) }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">配对日均差值</span>
        <strong class="stat-value" data-testid="diff-mean-diff">{{ formatNumber(diff.mean_diff) }}</strong>
      </article>
    </div>

    <table v-if="diff" class="data-table">
      <thead>
        <tr>
          <th>业务日期</th>
          <th>口径 v{{ diff.version_a }} 采样</th>
          <th>口径 v{{ diff.version_b }} 采样</th>
          <th>差值</th>
          <th>差异百分比</th>
          <th>配对状态</th>
          <th>可单独核对</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in displayRows"
          :key="row.business_date"
          :data-testid="`diff-row-${row.business_date}`"
          :data-pair-state="row.pair_state"
        >
          <td>{{ row.business_date }}</td>
          <td v-if="row.pair_state === 'no_sample'" colspan="2" class="muted">无采样</td>
          <template v-else>
            <td>
              <template v-if="row.sample_a">
                {{ formatNumber(row.sample_a.value) }}
                <small class="muted">（{{ row.sample_a_no }}）</small>
                <small v-if="!row.a_verified" class="error-text">未验证</small>
              </template>
              <span v-else class="error-text" data-testid="missing-a">缺旧值</span>
            </td>
            <td>
              <template v-if="row.sample_b">
                {{ formatNumber(row.sample_b.value) }}
                <small class="muted">（{{ row.sample_b_no }}）</small>
                <small v-if="!row.b_verified" class="error-text">未验证</small>
              </template>
              <span v-else class="error-text" data-testid="missing-b">缺新值</span>
            </td>
          </template>
          <td>{{ row.diff === null || row.diff === undefined ? '—' : formatNumber(row.diff) }}</td>
          <td>{{ row.diff_pct === null || row.diff_pct === undefined ? '—' : `${row.diff_pct}%` }}</td>
          <td>
            <span :class="row.pair_state === 'paired' ? 'tag ok' : row.pair_state === 'no_sample' ? 'tag muted-tag' : 'tag warn'">
              {{ row.pair_label }}
            </span>
          </td>
          <td>{{ row.individually_verifiable ? '是' : '否' }}</td>
        </tr>
        <tr v-if="!displayRows.length">
          <td colspan="7" class="empty-state">所选区间内没有两版口径的采样</td>
        </tr>
      </tbody>
    </table>
    <p v-if="diff && diff.no_sample_dates.length" class="status-legend">
      区间内完全无采样的日期以“无采样”列出：{{ diff.no_sample_dates.join('、') }}
      （不补零，不参与配对，也不能用其他日期互补）
    </p>

    <section v-if="diff" class="sample-panel">
      <h4 class="section-title">补登采样</h4>
      <p class="page-desc">
        缺口径的日期只能等对应口径自己的采样补齐；补登后重新计算差异，不会用别的日期顶替。
      </p>
      <form class="filter-bar" @submit.prevent="submitSample">
        <label class="filter-item">
          <span>口径版本</span>
          <select v-model.number="sampleForm.version" data-testid="sample-version">
            <option :value="diff.version_a">v{{ diff.version_a }}（旧口径）</option>
            <option :value="diff.version_b">v{{ diff.version_b }}（新口径）</option>
          </select>
        </label>
        <label class="filter-item">
          <span>业务日期</span>
          <input v-model="sampleForm.businessDate" data-testid="sample-date" type="date" required />
        </label>
        <label class="filter-item">
          <span>采样值</span>
          <input v-model.number="sampleForm.value" data-testid="sample-value" type="number" step="any" required />
        </label>
        <button class="btn" data-testid="submit-sample" type="submit">补登并重算</button>
      </form>
    </section>

    <section v-if="verification" class="panel" data-testid="verification-panel">
      <header class="panel-head">
        <div>
          <h3>归因核查 #{{ verification.id }} · {{ verification.metric_name }}</h3>
          <p class="page-desc">
            口径 v{{ verification.version_a }} → v{{ verification.version_b }}，
            {{ verification.start_date }} ~ {{ verification.end_date }}；
            状态：{{ verification.status }}
            <span v-if="verification.refreshed" class="muted">（本次为重跑，已处置日期保持原样）</span>
          </p>
          <p v-if="verification.anomaly_analysis_id" class="page-desc">
            已生成归因分析器记录 #{{ verification.anomaly_analysis_id }}，可在「异常归因」模块查看。
          </p>
        </div>
        <div class="page-actions">
          <button class="btn" data-testid="refresh-verification" type="button" @click="refreshVerification">
            重跑核查
          </button>
        </div>
      </header>

      <table class="data-table">
        <thead>
          <tr>
            <th>业务日期</th>
            <th>旧口径值</th>
            <th>新口径值</th>
            <th>差值</th>
            <th>采样编号（旧/新）</th>
            <th>配对状态</th>
            <th>领取状态</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="day in verification.days"
            :key="day.id"
            :data-testid="`verification-row-${day.business_date}`"
            :data-claimed="day.claimed ? 'true' : 'false'"
          >
            <td>{{ day.business_date }}</td>
            <td>{{ day.value_a === null ? '缺' : formatNumber(day.value_a) }}</td>
            <td>{{ day.value_b === null ? '缺' : formatNumber(day.value_b) }}</td>
            <td>{{ day.diff === null ? '—' : formatNumber(day.diff) }}</td>
            <td>
              {{ day.sample_a_no ?? '—' }} / {{ day.sample_b_no ?? '—' }}
            </td>
            <td>{{ day.pair_label }}</td>
            <td>
              <span v-if="day.claimed" class="tag ok">
                {{ day.claimed_shift || '已领取' }} · {{ day.claimed_at }}
              </span>
              <span v-else class="muted">未领取</span>
            </td>
          </tr>
        </tbody>
      </table>

      <h4 class="section-title">交班</h4>
      <form class="filter-bar" @submit.prevent="submitHandover">
        <label class="filter-item">
          <span>交接范围</span>
          <select v-model="handoverForm.scope" data-testid="handover-scope">
            <option value="day">只交单日</option>
            <option value="week">整周交接</option>
          </select>
        </label>
        <label v-if="handoverForm.scope === 'day'" class="filter-item">
          <span>业务日期</span>
          <select v-model="handoverForm.businessDate" data-testid="handover-date">
            <option v-for="day in verification.days" :key="day.id" :value="day.business_date">
              {{ day.business_date }}（{{ day.pair_label }}{{ day.claimed ? '，已领取' : '' }}）
            </option>
          </select>
        </label>
        <label class="filter-item">
          <span>交班班组</span>
          <input v-model="handoverForm.fromShift" data-testid="handover-from" />
        </label>
        <label class="filter-item">
          <span>接班班组</span>
          <input v-model="handoverForm.toShift" data-testid="handover-to" />
        </label>
        <button class="btn primary" data-testid="create-handover" type="submit">发起交班</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>交班批次</th>
            <th>范围</th>
            <th>状态</th>
            <th>可领取日期</th>
            <th>已领取日期</th>
            <th>说明</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="handover in verification.handovers"
            :key="handover.id"
            :data-testid="`handover-row-${handover.id}`"
          >
            <td>#{{ handover.id }}</td>
            <td>{{ handover.scope === 'day' ? `单日 ${handover.business_date}` : '整周' }}</td>
            <td>{{ handover.status }}</td>
            <td>{{ handover.claimable_dates.join('、') || '—' }}</td>
            <td>{{ handover.claimed_dates.join('、') || '—' }}</td>
            <td class="note-cell">{{ handover.note }}</td>
            <td>
              <button
                v-if="handover.status === '待领取'"
                class="link"
                :data-testid="`claim-handover-${handover.id}`"
                type="button"
                @click="claimHandover(handover.id)"
              >
                日班领取
              </button>
              <span v-else class="muted">—</span>
            </td>
          </tr>
          <tr v-if="!verification.handovers.length">
            <td colspan="7" class="empty-state">尚无交班记录</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span v-if="diff">指标「{{ diff.metric_name }}」共 {{ diff.days.length }} 个有采样的业务日期</span>
      <span v-if="errorMessage" class="error-text" data-testid="diff-error">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  claimCalibrationHandover,
  createCalibrationHandover,
  createCalibrationSample,
  createCalibrationVerification,
  getCalibrationVerification,
  listCalibrationMetrics,
  loadCalibrationDiff,
  refreshCalibrationVerification,
} from '@/api/backend-service'
import type {
  CalibrationDiff,
  CalibrationMetric,
  CalibrationVerification,
} from '@/data/types'

function offsetDate(days: number): string {
  const day = new Date()
  day.setDate(day.getDate() + days)
  return day.toISOString().slice(0, 10)
}

const metrics = ref<CalibrationMetric[]>([])
const versions = ref<number[]>([])
const diff = ref<CalibrationDiff | null>(null)
const verification = ref<CalibrationVerification | null>(null)
const errorMessage = ref('')
const creatingVerification = ref(false)

const form = ref({
  metricId: 0,
  versionA: 1,
  versionB: 2,
  startDate: offsetDate(-8),
  endDate: offsetDate(-2),
})
const handoverForm = ref({
  scope: 'day' as 'day' | 'week',
  businessDate: '',
  fromShift: '夜班',
  toShift: '日班',
})
const sampleForm = ref({
  version: 1,
  businessDate: '',
  value: 0,
})

const selectedMetric = computed(
  () => metrics.value.find((item) => item.metric_id === form.value.metricId) ?? null,
)

// 完全没有采样的自然日也列出来，明确标注“无采样”，不补零、不参与差值。
const displayRows = computed(() => {
  if (!diff.value) return []
  const emptyDays = diff.value.no_sample_dates.map((businessDate) => ({
    business_date: businessDate,
    pair_state: 'no_sample' as const,
    pair_label: '无采样',
    individually_verifiable: false,
    value_a: null,
    value_b: null,
    diff: null,
    diff_pct: null,
    sample_a_id: null,
    sample_b_id: null,
    sample_a_no: null,
    sample_b_no: null,
    sample_a: null,
    sample_b: null,
    a_has_sample: false,
    b_has_sample: false,
    a_verified: false,
    b_verified: false,
  }))
  return [...diff.value.days, ...emptyDays].sort((a, b) =>
    a.business_date.localeCompare(b.business_date),
  )
})

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}

function syncVersions() {
  const metric = selectedMetric.value
  const existing = metric?.versions.map((item) => item.version) ?? []
  versions.value = existing.length ? existing : [1, 2]
  if (!versions.value.includes(form.value.versionA)) form.value.versionA = versions.value[0]
  if (!versions.value.includes(form.value.versionB)) {
    form.value.versionB = versions.value[1] ?? versions.value[0]
  }
  if (form.value.versionA === form.value.versionB && versions.value.length > 1) {
    form.value.versionB = versions.value.find((v) => v !== form.value.versionA) ?? form.value.versionB
  }
  // 默认把区间贴到该指标已有采样的首末业务日期，避免一进页面是空表。
  const firstDates = metric?.versions.map((item) => item.first_date).filter(Boolean) as string[]
  const lastDates = metric?.versions.map((item) => item.last_date).filter(Boolean) as string[]
  if (firstDates.length) {
    form.value.startDate = firstDates.slice().sort()[0]
  }
  if (lastDates.length) {
    form.value.endDate = lastDates.slice().sort().slice(-1)[0]
  }
}

async function loadDiff(keepVerification = false) {
  errorMessage.value = ''
  if (form.value.versionA === form.value.versionB) {
    errorMessage.value = '两个口径版本不能相同'
    return
  }
  try {
    diff.value = await loadCalibrationDiff({
      metric_id: form.value.metricId,
      version_a: form.value.versionA,
      version_b: form.value.versionB,
      start_date: form.value.startDate,
      end_date: form.value.endDate,
    })
    handoverForm.value.businessDate = diff.value.individually_verifiable_dates[0] ?? ''
    sampleForm.value.version = diff.value.version_a
    sampleForm.value.businessDate = diff.value.dates_without_pair[0] ?? ''
    if (!keepVerification) verification.value = null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '口径差异读取失败'
  }
}

async function createVerification() {
  if (!diff.value) return
  creatingVerification.value = true
  errorMessage.value = ''
  try {
    verification.value = await createCalibrationVerification({
      metric_id: diff.value.metric_id,
      version_a: diff.value.version_a,
      version_b: diff.value.version_b,
      start_date: diff.value.start_date,
      end_date: diff.value.end_date,
      requested_by: '值班主管',
    })
    handoverForm.value.businessDate = diff.value.individually_verifiable_dates[0] ?? ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '发起归因核查失败'
  } finally {
    creatingVerification.value = false
  }
}

async function submitSample() {
  if (!diff.value) return
  errorMessage.value = ''
  try {
    await createCalibrationSample({
      metric_id: diff.value.metric_id,
      version: sampleForm.value.version,
      business_date: sampleForm.value.businessDate,
      value: Number(sampleForm.value.value),
    })
    metrics.value = await listCalibrationMetrics()
    await loadDiff(true)
    if (verification.value) {
      verification.value = await getCalibrationVerification(verification.value.id)
    }
    const target = sampleForm.value.businessDate
    if (target) handoverForm.value.businessDate = target
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补登采样失败'
  }
}

async function refreshVerification() {
  if (!verification.value) return
  errorMessage.value = ''
  try {
    verification.value = await refreshCalibrationVerification(verification.value.id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重跑核查失败'
  }
}

async function submitHandover() {
  if (!verification.value) return
  errorMessage.value = ''
  try {
    const payload: Parameters<typeof createCalibrationHandover>[1] = {
      scope: handoverForm.value.scope,
      from_shift: handoverForm.value.fromShift,
      to_shift: handoverForm.value.toShift,
    }
    if (handoverForm.value.scope === 'day') {
      payload.business_date = handoverForm.value.businessDate
    }
    await createCalibrationHandover(verification.value.id, payload)
    verification.value = await getCalibrationVerification(verification.value.id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '发起交班失败'
  }
}

async function claimHandover(handoverId: number) {
  errorMessage.value = ''
  try {
    await claimCalibrationHandover(handoverId, {
      claimed_by: '日班值班员',
      shift: '日班',
    })
    if (verification.value) {
      verification.value = await getCalibrationVerification(verification.value.id)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领取交班失败'
  }
}

onMounted(async () => {
  try {
    metrics.value = await listCalibrationMetrics()
    if (metrics.value.length) {
      // 优先定位到带多口径采样的指标（通常是口径切换演示指标）。
      const preferred = metrics.value.find(
        (item) => item.versions.length >= 2,
      ) ?? metrics.value[0]
      form.value.metricId = preferred.metric_id
    }
    syncVersions()
    if (form.value.metricId) await loadDiff()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '指标列表读取失败'
  }
})
</script>

<style scoped>
select {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  background: #fff;
}
.muted { color: var(--muted); }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.tag.ok { background: #e7f6ec; color: #18794e; }
.tag.warn { background: #fdf0e5; color: #b54708; }
.tag.muted-tag { background: #eef1f5; color: var(--muted); }
.panel { margin-top: 18px; }
.panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 8px; }
.section-title { margin: 18px 0 8px; font-size: 14px; }
.note-cell { max-width: 320px; font-size: 12px; color: var(--muted); }
</style>
