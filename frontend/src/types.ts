export type Source = 'ac' | 'demo'
export interface Tyre {
  corner: string
  core: number | null
  inner: number | null
  middle: number | null
  outer: number | null
  surface: number | null
  pressure: number | null
  wear_raw: number | null
  load: number | null
  slip_raw: number | null
  angular_speed: number | null
  brake_temp: number | null
  travel: number | null
  locked: boolean | null
  spinning: boolean | null
}
export interface Sample {
  source: Source
  packet_id: number
  captured_at: number
  status: number
  completed_laps: number
  lap_ms: number
  last_lap_ms: number
  best_lap_ms: number
  sector_index: number
  last_sector_ms: number
  position: number
  session_left_ms: number | null
  lap_pos: number
  coords: [number, number, number]
  in_pit: boolean
  tyres_out: number
  penalty_s: number
  channels: Record<string, number | null>
  tyres: Tyre[]
}
export interface Meta {
  source: Source
  driver: string
  car: string
  track: string
  layout: string
  session_type: number
  track_length: number
  sector_count: number
  max_rpm: number
  compound: string
  air_temp: number | null
  road_temp: number | null
}
export interface Tip {
  corner: number | null
  position_m: number
  measurement: Record<string, string | number>
  cause: string
  action: string
  potential_s: number | null
  observed_loss_s: number | null
  confidence: string
  corner_estimated: boolean
}
export interface Statistics {
  count?: number
  best_s?: number | null
  mean_s?: number | null
  std_s?: number | null
  last_five_std_s?: number | null
  theoretical_s?: number | null
  sectors_s?: Array<number | null>
  mini_sectors_s?: Array<number | null>
  combined_mini_s?: number | null
  outliers?: string[]
  fuel_effect?: { seconds_per_litre: number } | null
  stint?: Array<{
    lap: number
    time_s: number
    fuel_l: number
    tyres: Record<string, number | null>
  }>
}
export interface Frame {
  type: string
  /** Game/data-source state from the backend (see status.ts). */
  status: string
  /** Data flowing from the selected source (demo or game). */
  connected: boolean
  source?: Source | null
  /** Real Assetto Corsa telemetry only; never true for demo. */
  game_connected?: boolean
  source_message?: string | null
  /** Dashboard bundle id; a change means the PC app was updated. */
  build?: string
  /** Lap-timing block from the backend (game times only; see backend timing.py). */
  timing?: Timing | null
  error: string | null
  meta: Meta | null
  sample: Sample | null
  session_id: string | null
  reference_id: string | null
  reference_ms: number | null
  delta_s: number | null
  personal_best_ms?: number | null
  personal_best_delta_s?: number | null
  ghost_pos: number | null
  ghost_coords?: [number, number] | null
  fuel_laps_est: number | null
  current_sector_ms: number | null
  statistics: Statistics
  tips: Tip[]
  recording: boolean
  storage: { size_mb?: number; limit_exceeded?: boolean }
  tyre_warnings: Array<{
    wheel: string
    temperature: string
    pressure: string
    locked_est: boolean | null
    wheelspin_est: boolean | null
  }>
  performance: { samples: number; dropped: number; queue_depth: number; uptime_s: number }
}
export interface Settings {
  language: 'de' | 'en'
  units: 'metric' | 'imperial'
  capture_hz: number
  broadcast_hz: number
  source: Source
  auto_save: boolean
  storage_mb: number
  lan: boolean
  port: number
  token_set: boolean
  temp_cold: number
  temp_hot: number
  pressure_low: number
  pressure_high: number
  steering_lock_deg: number | null
  wheelbase_m: number | null
  steering_ratio: number | null
  steering_yaw_sign: number | null
  brake_threshold: number
  full_throttle: number
  lock_ratio: number
  spin_ratio: number
  slip_angle_deg: number
  video_mode: 'none' | 'url' | 'file' | 'screen' | 'camera'
  video_url: string
  video_device?: string
  /** Video position = telemetry time + offset (positive: picture lags). */
  video_offset_s: number
  record_auto?: boolean
  record_quality?: 'saver' | 'standard' | 'high'
  /** Sound with the video: only Assetto Corsa, the whole PC, or none. */
  record_audio?: 'game' | 'system' | 'off'
  record_storage_mb?: number
  record_delete_oldest?: boolean
  record_dir?: string
  visible_widgets: string[]
  widget_order: string[]
  graph_colors: Record<string, string>
  reference_lap_id: string | null
  hud_hold_s?: number
  compound_map?: Record<string, string>
  start_with_windows: boolean
  update_check?: boolean
  open_browser: boolean
  ai_provider?: 'off' | 'anthropic'
  ai_model?: string
  ai_consent?: number
  ac_install_dir?: string
  ac_setups_dir?: string
  upload_telemetry: false
}
export interface VersionInfo {
  name: string
  version: string
  update_check: boolean
  download_url: string | null
  update: { version: string; url: string; published_at: string | null } | null
  error: string | null
}
export interface Event {
  type: string
  distance_m: number
  end_m: number
  time_s: number
  estimated: boolean
  corner?: number
  speed_kmh?: number
  wheel?: string
  gear?: number
  peak?: number
}
export interface Track {
  points: Array<[number, number, number]>
  sector_positions: number[]
  events: Event[]
  complete: boolean
  source: Source | null
  length_m?: number
  /** Provisional map only: share of the lap already driven (0..1). */
  coverage?: number
  /** Built from an already stored lap that covered the whole track. */
  recovered?: boolean
}
export interface LapSummary {
  id: string
  session_id: string
  number: number
  duration_ms: number
  valid: boolean
  complete: boolean
  reasons: string[]
  sectors_ms: Array<number | null>
  sector_positions?: number[]
  events: Event[]
  tips: Tip[]
  /** Onboard video of this lap (sessions recorded with video only). */
  video?: LapVideoStatus
}
export interface LapVideoStatus {
  coverage: 'full' | 'partial' | 'none' | 'pending'
  recording_id: string | null
  covered_ratio: number | null
  /** The video of this lap has sound. */
  audio?: boolean
}
export interface Session {
  id: string
  created_at: string
  meta: Meta
  lap_count: number
  best_ms: number | null
  favorite: boolean
  ended_at: string | null
  video_count?: number
  recordings?: import('./playback').RecordingInfo[]
  laps?: LapSummary[]
  statistics?: Statistics
}
export interface Compare {
  distance: Array<number>
  delta: Array<number | null>
  line_difference_m: Array<number | null>
  lap: { id: string; time: Array<number | null>; channels: Record<string, Array<number | null>> }
  reference: {
    id: string
    time: Array<number | null>
    channels: Record<string, Array<number | null>>
  }
  map: { x: Array<number | null>; z: Array<number | null> }
  reference_map?: { x: Array<number | null>; z: Array<number | null> }
  summary: { lap_s: number; reference_s: number; delta_s: number }
  events: Event[]
  tips: Tip[]
  corners: Array<{
    corner: number
    start_m: number
    apex_m: number
    end_m: number
    loss_s: number | null
    lap: Record<string, number | null>
    reference: Record<string, number | null>
  }>
}

export interface PageDiagnostics {
  name: string
  present: boolean
  readable: boolean
  initialized: boolean
  read_only: boolean | null
  expected_bytes: number
  read_bytes: number
  mapped_bytes: number | null
  error_code: number | null
  error: string | null
}
export interface Diagnostics {
  generated_at: string
  status: string
  system: {
    os: string
    windows: boolean
    python: string
    python_bits: number
    machine: string
    frozen: boolean
  }
  source: { selected: Source; active: Source | null }
  capture_thread: {
    alive: boolean
    restarts: number
    errors: number
    last_error: string | null
    last_error_at: string | null
    exit_reason: string | null
  }
  game: {
    state: string
    message: string
    process: { status: string; game: string[]; launcher: string[]; error: string | null }
    pages: PageDiagnostics[]
    physics_packet_id: number | null
    graphics_packet_id: number | null
    game_status: { code: number | null; name: string | null }
    last_update_age_s: number | null
    last_decode_at: string | null
    last_decode_error: string | null
    last_decode_error_at: string | null
    decode_errors: number
    torn_snapshots: number
    connects: number
    samples: number
    observed_hz: number
    unknown_fields: string[]
  } | null
  engine: {
    analysis_thread_alive: boolean
    samples: number
    dropped: number
    queue_depth: number
    last_sample_age_s: number | null
    last_sample_source: Source | null
    analysis_error: string | null
  }
  websocket: { clients: number; last_send_age_s: number | null }
  hints: Array<{ level: 'info' | 'warning' | 'error'; de: string; en: string }>
}

export interface TimingReference {
  id: string
  kind: 'selected' | 'personal_best'
  number: number
  duration_ms: number
  sectors_ms: Array<number | null>
}
export interface TimingLastLap {
  number: number
  duration_ms: number
  sectors_ms: Array<number | null>
  valid: boolean
  complete: boolean
  reasons: string[]
  invalid_reasons: string[]
  reference: TimingReference | null
  best_sectors_ms: Array<number | null>
  age_ms: number
}
export interface Timing {
  lap_number: number
  lap_ms: number
  game_status: number
  sector_index: number
  sector_count: number
  sectors_known: boolean
  sectors_ms: Array<number | null>
  started_at_line: boolean
  reasons: string[]
  invalid_reasons: string[]
  last_lap: TimingLastLap | null
  reference: TimingReference | null
  best_sectors_ms: Array<number | null>
  hold_ms: number
  resolution_ms: number
}
