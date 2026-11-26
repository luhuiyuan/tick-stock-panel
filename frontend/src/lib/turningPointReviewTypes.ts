import type { OHLC } from './chart-types'

export type TurningDirection = 'high' | 'low'
export type TurningCaseType = 'matched' | 'unmatched' | 'missed' | 'duplicate' | 'indeterminate_pivot' | 'indeterminate_alert'
export type TurningScope = { symbols: string; start: string; end: string; signal_ids: string }
export type TurningSignal = {
  signal_id: string; name: string; type: 'atom' | 'composite'; timeframe: string; description: string; enabled: boolean
}
export type TurningSummary = {
  signal_id: string; direction: TurningDirection; pivot_coverage: number | null; alert_match_rate: number | null
  covered_count: number; missed_count: number; matched_alert_count: number; unmatched_alert_count: number
  duplicate_alert_count: number; indeterminate_pivot_count: number; indeterminate_alert_count: number
  evaluated_symbols: number; unavailable_symbols: number; lead_windows: Record<'1_5' | '6_15' | '16_30', number>
}
export type TurningResult = {
  snapshot_sha256: string; missing_market_partitions: string[]; errors: { symbol: string; reason: string }[]
  summary: TurningSummary[]
  results: { data: { symbol: string }; signals: (TurningSignal & {
    status: 'ok' | 'partial' | 'unavailable'; reason?: string; known_bars?: number; unknown_bars?: number
    limit_price_sources?: Record<string, { recorded: number; inferred: number; unknown: number }>
  })[] }[]
}
export type TurningCase = {
  case_id: string; case_type: TurningCaseType; symbol: string; direction: TurningDirection
  pivot: { date: string; confirmed_date: string; type: TurningDirection; price: number } | null
  alert: { start_date: string; end_date: string; trigger_dates: string[] } | null
  lead_bars: number | null; adverse_excursion_5_bars: number | null
  bars: (Omit<OHLC, 'volume'> & { volume: number | null })[]; bars_truncated: boolean
}
export type TurningCasePage = Pick<TurningResult, 'snapshot_sha256' | 'errors' | 'missing_market_partitions'> & {
  total: number; offset: number; cases: TurningCase[]
}
