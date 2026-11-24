/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

// ==================== 口径差异核查 ====================

export type CalibrationSample = {
  id: number
  sample_no: string
  version: number
  business_date: string
  snapshot_time: string
  value: number
  verified: boolean
  status: string
}

export type CalibrationMetricVersion = {
  version: number
  sample_count: number
  first_date: string | null
  last_date: string | null
}

export type CalibrationMetric = {
  metric_id: number
  metric_name: string
  metric_type: string
  unit: string
  versions: CalibrationMetricVersion[]
}

export type CalibrationDiffDay = {
  business_date: string
  pair_state: 'paired' | 'missing_a' | 'missing_b'
  pair_label: string
  individually_verifiable: boolean
  value_a: number | null
  value_b: number | null
  diff: number | null
  diff_pct: number | null
  sample_a_id: number | null
  sample_b_id: number | null
  sample_a_no: string | null
  sample_b_no: string | null
  sample_a: CalibrationSample | null
  sample_b: CalibrationSample | null
  a_has_sample: boolean
  b_has_sample: boolean
  a_verified: boolean
  b_verified: boolean
}

export type CalibrationDiff = {
  ok: boolean
  metric_id: number
  metric_name: string
  unit: string
  version_a: number
  version_b: number
  start_date: string
  end_date: string
  days: CalibrationDiffDay[]
  paired_count: number
  missing_count: number
  total_dates: number
  dates_without_pair: string[]
  missing_by_state: { missing_a: string[]; missing_b: string[] }
  no_sample_dates: string[]
  individually_verifiable_dates: string[]
  mean_a: number | null
  mean_b: number | null
  mean_diff: number | null
}

export type CalibrationVerificationDay = {
  id: number
  business_date: string
  pair_state: string
  pair_label: string
  individually_verifiable: boolean
  value_a: number | null
  value_b: number | null
  diff: number | null
  diff_pct: number | null
  sample_a_id: number | null
  sample_b_id: number | null
  sample_a_no: string | null
  sample_b_no: string | null
  sample_a: CalibrationSample | null
  sample_b: CalibrationSample | null
  verified: boolean
  claimed: boolean
  claimed_shift: string | null
  claimed_at: string | null
}

export type CalibrationHandoverItem = {
  id: number
  business_date: string
  status: string
  claimed_shift: string | null
  claimed_by: string | null
  claimed_at: string | null
  value_a: number | null
  value_b: number | null
  diff: number | null
  sample_a_id: number
  sample_b_id: number
  sample_a_no: string | null
  sample_b_no: string | null
  sample_a?: CalibrationSample | null
  sample_b?: CalibrationSample | null
  pair_label?: string
}

export type CalibrationHandover = {
  ok: boolean
  id: number
  verification_id: number
  metric_id: number | null
  metric_name: string
  version_a: number | null
  version_b: number | null
  start_date: string | null
  end_date: string | null
  scope: 'day' | 'week'
  business_date: string | null
  from_shift: string
  to_shift: string
  status: string
  claimable_count: number
  claimed_count: number
  note: string
  created_at: string
  items: CalibrationHandoverItem[]
  claimable_dates: string[]
  claimed_dates: string[]
  blocked_dates?: { business_date: string; reason: string }[]
}

export type CalibrationVerification = {
  ok: boolean
  id: number
  metric_id: number
  metric_name: string
  unit: string
  version_a: number
  version_b: number
  start_date: string
  end_date: string
  title: string
  requested_by: string
  status: string
  anomaly_analysis_id: number | null
  created_at: string
  updated_at: string
  days: CalibrationVerificationDay[]
  paired_count: number
  missing_count: number
  individually_verifiable_dates: string[]
  dates_without_pair: string[]
  claimed_dates: string[]
  handovers: (CalibrationHandover & { item_count: number })[]
  reused?: boolean
  refreshed?: boolean
}

export type CalibrationVerificationSummary = {
  id: number
  metric_id: number
  metric_name: string
  version_a: number
  version_b: number
  start_date: string
  end_date: string
  title: string
  requested_by: string
  status: string
  anomaly_analysis_id: number | null
  date_count: number
  paired_count: number
  claimed_count: number
  created_at: string
  updated_at: string
}

export type AttributionHandover = {
  handover_id: number
  verification_id: number
  anomaly_analysis_id: number | null
  conclusion: string | null
  metric_name: string
  version_a: number | null
  version_b: number | null
  start_date: string | null
  end_date: string | null
  scope: 'day' | 'week'
  status: string
  from_shift: string
  to_shift: string
  claimable_dates: string[]
  claimed_dates: string[]
  items: {
    business_date: string
    status: string
    claimed_shift: string | null
    claimed_by: string | null
    claimed_at: string | null
    value_a: number | null
    value_b: number | null
    diff: number | null
    sample_a_no: string | null
    sample_b_no: string | null
    sample_a_id: number
    sample_b_id: number
  }[]
  note: string
  created_at: string
}
