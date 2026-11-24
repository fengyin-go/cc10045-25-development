import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  AttributionHandover,
  CalibrationDiff,
  CalibrationHandover,
  CalibrationMetric,
  CalibrationSample,
  CalibrationVerification,
  CalibrationVerificationSummary,
  EntryRow,
  OverviewResult,
  PageResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ==================== 口径差异核查与归因交班 ====================

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    let detail = fallback
    try {
      const payload = await response.json()
      if (payload?.detail) detail = String(payload.detail)
    } catch {
      /* 保留兜底文案 */
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export async function listCalibrationMetrics(): Promise<CalibrationMetric[]> {
  const response = await request('/api/calibration/metrics')
  return (await readJson<{ items: CalibrationMetric[] }>(response, '指标列表读取失败')).items
}

export async function loadCalibrationDiff(params: {
  metric_id: number
  version_a: number
  version_b: number
  start_date: string
  end_date: string
}): Promise<CalibrationDiff> {
  const query = new URLSearchParams(
    Object.entries(params).map(([key, value]) => [key, String(value)]),
  ).toString()
  const response = await request(`/api/calibration/diff?${query}`)
  return await readJson<CalibrationDiff>(response, '口径差异读取失败')
}

export async function listCalibrationSamples(params: {
  metric_id?: number
  version?: number
  start_date?: string
  end_date?: string
}): Promise<CalibrationSample[]> {
  const query = new URLSearchParams(
    Object.entries(params)
      .filter(([, value]) => value !== undefined && value !== '')
      .map(([key, value]) => [key, String(value)]),
  ).toString()
  const response = await request(`/api/calibration/samples?${query}`)
  return (await readJson<{ items: CalibrationSample[] }>(response, '采样列表读取失败')).items
}

export async function createCalibrationSample(payload: {
  metric_id: number
  version: number
  business_date: string
  value: number
  sample_no?: string
  verified?: boolean
}): Promise<CalibrationSample> {
  const response = await request('/api/calibration/samples', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  const result = await readJson<{ ok: boolean; sample: CalibrationSample }>(
    response, '补登采样失败',
  )
  return result.sample
}

export async function createCalibrationVerification(payload: {
  metric_id: number
  version_a: number
  version_b: number
  start_date: string
  end_date: string
  requested_by?: string
}): Promise<CalibrationVerification> {
  const response = await request('/api/calibration/verifications', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await readJson<CalibrationVerification>(response, '发起归因核查失败')
}

export async function refreshCalibrationVerification(
  id: number,
): Promise<CalibrationVerification> {
  const response = await request(`/api/calibration/verifications/${id}/refresh`, {
    method: 'POST',
  })
  return await readJson<CalibrationVerification>(response, '重跑核查失败')
}

export async function listCalibrationVerifications(): Promise<CalibrationVerificationSummary[]> {
  const response = await request('/api/calibration/verifications')
  return (await readJson<{ items: CalibrationVerificationSummary[] }>(
    response, '核查记录读取失败',
  )).items
}

export async function getCalibrationVerification(
  id: number,
): Promise<CalibrationVerification> {
  const response = await request(`/api/calibration/verifications/${id}`)
  return await readJson<CalibrationVerification>(response, '核查详情读取失败')
}

export async function createCalibrationHandover(
  verificationId: number,
  payload: {
    scope: 'day' | 'week'
    business_date?: string
    from_shift?: string
    to_shift?: string
    note?: string
  },
): Promise<CalibrationHandover> {
  const response = await request(
    `/api/calibration/verifications/${verificationId}/handovers`,
    { method: 'POST', body: JSON.stringify(payload) },
  )
  return await readJson<CalibrationHandover>(response, '发起交班失败')
}

export async function claimCalibrationHandover(
  handoverId: number,
  payload: { claimed_by?: string; shift?: string },
): Promise<CalibrationHandover> {
  const response = await request(`/api/calibration/handovers/${handoverId}/claim`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await readJson<CalibrationHandover>(response, '领取交班失败')
}

export async function listAttributionHandovers(shift = ''): Promise<{
  items: AttributionHandover[]
  shiftClaimedDates: string[]
}> {
  const query = shift ? `?shift=${encodeURIComponent(shift)}` : ''
  const response = await request(`/api/calibration/attribution-handovers${query}`)
  const payload = await readJson<{
    items: AttributionHandover[]
    shift_claimed_dates?: string[]
  }>(response, '归因交班记录读取失败')
  return { items: payload.items, shiftClaimedDates: payload.shift_claimed_dates ?? [] }
}
