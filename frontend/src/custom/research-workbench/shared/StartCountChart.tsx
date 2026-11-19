import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import * as echarts from 'echarts'
import { useChartTheme } from '@/lib/theme'
import type { RecipeSlotProps } from './types'
const BULL = '#C74040'
const SELECTED_BAR = '#E2B15A'

export function StartCountChart({ ctx, setExtra, rebuilding, keyPrefix, title, filters, load }: RecipeSlotProps & {
  title: string
  filters: readonly unknown[]
  load: (bucket: 'day' | 'month') => Promise<{ computed?: boolean; points: { date: string; count: number }[] }>
}) {
  const ct = useChartTheme()
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  const bucket = (ctx.extra.bucket as 'day' | 'month') ?? 'day'
  const selected = (ctx.extra.started as string) ?? ''
  const setExtraRef = useRef(setExtra)
  setExtraRef.current = setExtra
  const startedRef = useRef(selected)
  startedRef.current = selected

  const query = useQuery({
    queryKey: [...keyPrefix, 'starts', ctx.start, ctx.end, ...filters, bucket],
    queryFn: () => load(bucket),
  })
  const points = query.data?.points ?? []

  useEffect(() => {
    if (!box.current || points.length === 0) {
      chart.current?.dispose()
      chart.current = null
      return
    }
    const inst = chart.current ?? echarts.init(box.current, undefined, { renderer: 'canvas' })
    chart.current = inst
    inst.setOption({
      animation: false,
      grid: { left: 48, right: 16, top: 16, bottom: bucket === 'day' ? 52 : 28 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: ct.tooltipBg,
        borderColor: ct.tooltipBorder,
        textStyle: { color: ct.tooltipText, fontSize: 12 },
        formatter: (items: unknown) => {
          const row = (Array.isArray(items) ? items[0] : null) as { axisValue?: string; value?: unknown; data?: unknown } | null
          if (!row) return ''
          const v = row.value ?? row.data
          const count = typeof v === 'number' ? v : (v && typeof v === 'object' && 'value' in v && typeof v.value === 'number' ? v.value : 0)
          return `${row.axisValue}<br/>启动 ${count} 段`
        },
      },
      xAxis: {
        type: 'category', data: points.map(point => point.date),
        axisLabel: { color: ct.text, fontSize: 10, hideOverlap: true },
        axisLine: { lineStyle: { color: ct.border } }, axisTick: { show: false },
      },
      yAxis: {
        type: 'value', minInterval: 1,
        axisLabel: { color: ct.text, fontSize: 10 },
        splitLine: { lineStyle: { color: ct.grid } },
      },
      dataZoom: bucket === 'day' ? [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 8 }] : [],
      series: [{
        type: 'bar', cursor: 'pointer', barMaxWidth: bucket === 'month' ? 28 : 12,
        data: points.map(point => ({
          value: point.count,
          itemStyle: { color: point.date === selected ? SELECTED_BAR : BULL },
        })),
      }],
    }, true)
    inst.off('click')
    inst.on('click', (params: { componentType?: string; name?: string }) => {
      if (params.componentType === 'series' && params.name) {
        const value = startedRef.current === params.name ? '' : params.name
        setExtraRef.current({ started: value })
      }
    })
    const onResize = () => inst.resize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [points, bucket, ct, selected])

  useEffect(() => () => { chart.current?.dispose(); chart.current = null }, [])

  const total = points.reduce((sum, point) => sum + point.count, 0)
  return (
    <div className="shrink-0 border-b border-border px-5 py-2">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-xs text-secondary">{title}</span>
        <button type="button" className={`h-7 rounded-btn px-2 text-xs ${bucket === 'day' ? 'bg-elevated text-foreground' : 'text-muted'}`} onClick={() => setExtra({ bucket: 'day', started: '' })}>按日</button>
        <button type="button" className={`h-7 rounded-btn px-2 text-xs ${bucket === 'month' ? 'bg-elevated text-foreground' : 'text-muted'}`} onClick={() => setExtra({ bucket: 'month', started: '' })}>按月</button>
        {selected && (
          <button type="button" className="h-7 rounded-btn px-2 text-xs text-secondary hover:text-foreground" onClick={() => setExtra({ started: '' })}>清除选择 {selected}</button>
        )}
        {query.data && <span className="text-[11px] text-muted">合计 {total} 段</span>}
      </div>
      {(query.isLoading || rebuilding) ? (
        <div className="flex h-52 items-center justify-center text-xs text-muted">正在统计启动个数…</div>
      ) : query.isError ? (
        <div className="flex h-24 items-center justify-center text-xs text-muted">
          启动个数没有读出来。
          <button type="button" className="ml-2 underline" onClick={() => query.refetch()}>重试</button>
        </div>
      ) : query.data?.computed === false ? (
        <div className="flex h-24 items-center justify-center text-xs text-muted">还没有计算，请点重新计算。</div>
      ) : points.length === 0 ? (
        <div className="flex h-24 items-center justify-center text-xs text-muted">这个条件下没有启动。</div>
      ) : (
        <div ref={box} className="h-52 w-full" />
      )}
    </div>
  )
}
