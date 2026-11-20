import type { CoverageEvent } from '@/lib/api'
import type { ChartMarker, ChartRange } from '@/lib/chart-types'
import { coverageWindowLabel } from './CoverageCaseCard'

export function coveragePreview(event: CoverageEvent, signal: string, rule?: string) {
  const { dates, flags, start_date: start, end_date: end, bars } = event
  const first = dates[0], last = dates.at(-1)
  const label = coverageWindowLabel(rule)
  const dateRange = {
    start: [start, first ?? start, bars[0]?.date ?? start].sort()[0],
    end: [end, bars.at(-1)?.date ?? end].sort()[1],
  }
  const markers: ChartMarker[] = dates.flatMap((date, i) => flags[i] === true
    ? [{ date, kind: 'neutral', above: true, label: '触发', color: '#eab308' }] : [])
  markers.push({ date: start, kind: 'neutral', above: true, label: 'T 起始' })
  const ranges: ChartRange[] = [
    { start, end, label: '猛拉区间', color: 'rgba(199,64,64,.32)' },
  ]
  if (first && last) ranges.unshift({ start: first, end: last, label, color: 'rgba(39,175,159,.18)' })
  const observations = dates.map((d, i) => `${d.slice(5)} ${flags[i] == null ? '未知' : flags[i] ? '触发' : '否'}`).join(' / ')
  const summary = `${signal} · 猛拉 ${start} 至 ${end} · ${label}：${observations || '无历史日 K'}`
  return { dateRange, markers, ranges, summary }
}
