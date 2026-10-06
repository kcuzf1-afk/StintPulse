/** Setup assistant (Analyse → Setup): shapes of the /api/setup/* responses. */

export type ParamStatus = 'abgeleitet' | 'bestaetigt' | 'mehrdeutig' | 'widerspruechlich' | 'unbestaetigt' | 'nicht_unterstuetzt'
export type EncodingKind = 'value' | 'tenths' | 'clicks'

export interface SetupParam {
  name: string
  label: string
  tab: string
  unit: string
  status: ParamStatus
  reason: string
  exportable: boolean
  in_setup: boolean
  raw: string | null
  stored: number | null
  encoding: EncodingKind | null
  options: Array<{ kind: EncodingKind; label: string; display: number | null }>
  observed: number
  min?: number
  max?: number
  spec_step?: number
  step?: number
  value?: number | null
  click?: number
}

export interface SpecSource {
  kind: 'game' | 'import' | 'missing' | 'error'
  path?: string
  imported_at?: string
  packed?: boolean
  game_found?: boolean
  error?: string
}

export interface CarInfo {
  car: string
  track: string
  spec: SpecSource
  observed_files: number
  counts: Partial<Record<ParamStatus, number>>
  params: SetupParam[]
  saved_setups: Array<{ folder: string; name: string; modified: number; bytes: number }>
}

export interface SetupVersion {
  id: string
  created_at: string
  car: string
  track: string
  kind: 'base' | 'export'
  parent_id: string | null
  analysis_id: string | null
  name: string
  origin: string
  sha256: string
  changes: BeforeAfter[]
  saved_path: string | null
  sessions?: number
}

export interface BaseResponse {
  version: SetupVersion
  params: SetupParam[]
  spec: SpecSource
  preserved: { sections: number; internal: number; duplicates: string[] }
}

export interface AiStatus {
  provider: 'off' | 'anthropic'
  model: string
  state: 'off' | 'sdk_missing' | 'no_key' | 'key_unreadable' | 'consent' | 'ready'
  ready: boolean
  key: { set: boolean; source: 'stored' | 'env' | null; unreadable: boolean; protection: 'dpapi' | 'file' }
  sdk: boolean
  consent: boolean
  disclosure_version: number
  providers: Record<string, { label: string; endpoint: string; privacy: string }>
  default_model: string
  pc: boolean
}

export interface SetupStatus {
  ai: AiStatus
  ac: { install_dir: string | null; setups_dir: string; setups_found: boolean }
  goals: Record<string, string>
  issues: Record<string, string>
  phases: Record<string, string>
}

export interface FeedbackItem {
  issue: string
  phase: string
  corners: string
  severity: 1 | 2 | 3
}

export interface Availability {
  key: string
  label: string
  ratio: number
  available: boolean
}

export interface PhaseFigures {
  slip_balance: number | null
  body_slip_deg: number | null
  steer_per_g: number | null
  corner_phases: number
}

export interface TyreFigures {
  inner: number | null
  middle: number | null
  outer: number | null
  core: number | null
  pressure_hot: number | null
  wear_per_lap: number | null
}

export interface KpiResult {
  laps: Array<{ id: string; session_id: string; number: number; duration_ms: number; used: boolean; reasons: string[]; notes?: string[] }>
  used: number
  excluded: number
  warnings: string[]
  notes: string[]
  availability: Availability[]
  kpis: {
    lap_time?: { median_s: number; best_s: number; spread_s: number; stdev_s: number }
    sectors_median_s?: Array<number | null>
    top_speed_kmh?: number | null
    phases?: Record<'entry' | 'mid' | 'exit', PhaseFigures>
    mid_corner_slip_balance_by_speed?: Record<string, { median: number | null; corners: number }>
    events_per_lap?: Record<string, number>
    traction_apex_to_full_throttle_s?: number | null
    tyres?: Record<'FL' | 'FR' | 'RL' | 'RR', TyreFigures>
    ride_height_min_m?: { front: number | null; rear: number | null }
    brake_temp_max_c?: { front: number | null; rear: number | null }
  }
  conditions: {
    fuel_start_l: [number, number] | null
    air_c: [number, number] | null
    road_c: [number, number] | null
    compounds: string[]
    sessions: number
  }
}

export interface Evidence {
  item: number
  issue: string
  phase: string
  label: string
  verdict: 'stützt' | 'widerspricht eher' | 'nicht eindeutig' | 'nicht messbar'
  details: string[]
}

export interface Recommendation {
  parameter: string
  label: string
  tab: string
  unit: string
  old_value: number | null
  new_value: number
  old_raw: string | null
  new_raw: string | null
  ini: string
  encoding: EncodingKind | null
  exportable: boolean
  export_block: string | null
  priority: number
  confidence: 'niedrig' | 'mittel' | 'hoch'
  confidence_limited: boolean
  observation: string
  possible_cause: string
  test: string
  reasoning: string
  expected_effect: string
  tradeoffs: string
}

export interface AnalysisResult {
  summary: string
  observations: Array<{ text: string; basis: 'messung' | 'rueckmeldung' | 'beides'; certainty: string }>
  changes: Recommendation[]
  dropped: Array<{ parameter: string; label: string; new_value: number; reason: string }>
  driving_tips: string[]
  uncertainties: string[]
  validation_notes: string[]
  error_code?: string
  provider_meta?: { model?: string; input_tokens?: number; output_tokens?: number }
}

export interface SetupAnalysis {
  id: string
  created_at: string
  car: string
  track: string
  base_version_id: string
  lap_ids: string[]
  goal: string
  feedback: { items: FeedbackItem[]; text: string; goal_text: string }
  provider: 'anthropic' | 'rules'
  model: string | null
  status: 'ok' | 'rules' | 'error'
  kpis: { kpis: KpiResult; evidence: Evidence[] }
  sent: { provider: string; model: string; payload: unknown } | null
  result: AnalysisResult
  error: string | null
}

export interface Preview {
  kpis: KpiResult
  evidence: Evidence[]
  request: unknown
  provider: { id: string; model: string; label?: string; endpoint?: string; privacy?: string }
}

export interface BeforeAfter {
  parameter: string
  label: string
  unit: string
  old_value: number | null
  new_value: number
  old_raw: string | null
  new_raw: string
}

export interface ExportResponse {
  version: SetupVersion
  before_after: BeforeAfter[]
  file_name: string
}

export interface Assignment {
  session_id: string
  version_id: string
  assigned_at: string
  note: string
  manual: true
}

export interface CompareSide {
  version: SetupVersion
  sessions: number
  notes: string[]
  manual: true
  kpis: KpiResult | null
  error?: string
  layout?: string
  feedback_before?: { items: FeedbackItem[]; text: string } | null
}

export interface CompareResponse {
  a: CompareSide
  b: CompareSide
  caveats: string[]
}
