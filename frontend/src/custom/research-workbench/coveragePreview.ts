import type { CoverageEvent } from '@/lib/api'
import type { ChartMarker, ChartRange } from '@/lib/chart-types'

export function coveragePreview(event: CoverageEvent, signal: string) {
  const { dates, flags, start_date: start, end_date: end, bars } = event
  const dateRange = {
    start: [dates[0], bars[0]?.date ?? dates[0]].sort()[0],
    end: [end, bars.at(-1)?.date ?? end].sort()[1],
  }
  const markers: ChartMarker[] = dates.flatMap((date, i) => flags[i] === true
    ? [{ date, kind: 'neutral', above: true, label: '触发', color: '#eab308' }] : [])
  markers.push({ date: start, kind: 'neutral', above: true, label: 'T 起始' })
  const ranges: ChartRange[] = [
    { start: dates[0], end: dates[4], label: '启动前五日', color: 'rgba(39,175,159,.18)' },
    { start, end, label: '猛拉区间', color: 'rgba(199,64,64,.32)' },
  ]
  const summary = `${signal} · 猛拉 ${start} 至 ${end} · 启动前五日：` +
    dates.map((d, i) => `${d.slice(5)} ${flags[i] == null ? '未知' : flags[i] ? '触发' : '否'}`).join(' / ')
  return { dateRange, markers, ranges, summary }
}
