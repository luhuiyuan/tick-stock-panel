import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as echarts from 'echarts'
import { ChevronLeft, ChevronRight, Search, X } from 'lucide-react'
import { DatePicker } from '@/components/DatePicker'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { PageHeader } from '@/components/PageHeader'
import { IconRiseResearch } from '@/components/BrandIcons'
import { api, type LimitUpResearchBar, type RiseResearchRun } from '@/lib/api'
import { useChartTheme } from '@/lib/theme'
import { QK } from '@/lib/queryKeys'
import { useListNav } from '@/lib/useListNav'
import { useDialogBackdrop } from '@/lib/useDialogBackdrop'
import type { FrontendExtension } from '@/extensions/types'

const PAGE_SIZE = 24
const BULL = '#C74040'
const BEAR = '#2D9B65'
const AFTER = 'rgba(45,155,101,0.16)'

function runKey(row: RiseResearchRun) {
  return row.id
}

function formatSpan(row: RiseResearchRun) {
  return row.start_date === row.end_date ? row.start_date : `${row.start_date} ~ ${row.end_date}`
}

function formatGain(gain: number) {
  const pct = gain * 100
  const digits = Math.abs(pct) >= 100 ? 0 : 1
  return `+${pct.toFixed(digits)}%`
}

function barSides(bars: { date: string }[], start: string, end: string) {
  let before = 0
  let after = 0
  for (const bar of bars) {
    if (bar.date < start) before += 1
    else if (bar.date > end) after += 1
  }
  return { before, after }
}

function MiniRunChart({ bars, start, end }: { bars: LimitUpResearchBar[]; start: string; end: string }) {
  const width = 280
  const height = 96
  if (bars.length === 0) {
    return <div className="grid h-24 place-items-center text-xs text-muted">这段没有可画的 K 线</div>
  }
  let hi = -Infinity
  let lo = Infinity
  for (const bar of bars) {
    if (bar.high > hi) hi = bar.high
    if (bar.low < lo) lo = bar.low
  }
  const range = hi - lo || 1
  const barW = width / bars.length
  const y = (value: number) => 4 + (1 - (value - lo) / range) * (height - 8)
  const startIdx = bars.findIndex(bar => bar.date >= start)
  let endIdx = -1
  for (let i = bars.length - 1; i >= 0; i -= 1) {
    if (bars[i].date <= end) {
      endIdx = i
      break
    }
  }
  const band = startIdx >= 0 && endIdx >= startIdx
    ? { x: startIdx * barW, width: (endIdx - startIdx + 1) * barW }
    : null
  const afterIdx = bars.findIndex(bar => bar.date > end)
  const afterBand = afterIdx >= 0
    ? { x: afterIdx * barW, width: (bars.length - afterIdx) * barW }
    : null
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} className="block" role="img" aria-label="上涨段缩略K线">
      {afterBand && <rect x={afterBand.x} y={0} width={afterBand.width} height={height} fill={AFTER} />}
      {band && <rect x={band.x} y={0} width={band.width} height={height} fill="rgba(199,64,64,0.14)" />}
      {bars.map((bar, index) => {
        const color = bar.close >= bar.open ? BULL : BEAR
        const cx = index * barW + barW / 2
        const bodyW = Math.max(barW * 0.55, 1.5)
        const top = y(Math.max(bar.open, bar.close))
        const bottom = y(Math.min(bar.open, bar.close))
        return (
          <g key={bar.date}>
            <line x1={cx} x2={cx} y1={y(bar.high)} y2={y(bar.low)} stroke={color} strokeWidth={1} />
            <rect x={cx - bodyW / 2} y={top} width={bodyW} height={Math.max(bottom - top, 1)} fill={color} />
          </g>
        )
      })}
    </svg>
  )
}

function RunDialog({
  rows,
  currentId,
  asOf,
  onSelect,
  onClose,
}: {
  rows: RiseResearchRun[]
  currentId: string
  asOf: string
  onSelect: (id: string) => void
  onClose: () => void
}) {
  const backdrop = useDialogBackdrop(onClose)
  const nav = useListNav({
    items: rows,
    keyOf: runKey,
    currentKey: currentId,
    onNavigate: row => onSelect(row.id),
    wrapHints: { head: '已到本页第一条', tail: '已到本页最后一条' },
  })
  const row = rows.find(item => item.id === currentId) ?? null
  const [frame, setFrame] = useState<'run' | 'year'>('run')
  useEffect(() => { setFrame('run') }, [currentId])
  const focus = useMemo(() => {
    if (!row || frame === 'year') return 'all' as const
    return { start: row.start_date, end: row.end_date, padding: 20 }
  }, [frame, row])
  const zoom = useQuery({
    queryKey: QK.riseResearch(`zoom|${currentId}`),
    enabled: row != null,
    queryFn: () => api.riseResearchZoom({
      symbol: row?.symbol ?? '',
      start: row?.start_date ?? '',
      end: row?.end_date ?? '',
    }),
  })
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  if (!row) return null
  const bars = zoom.data?.bars ?? []
  const data = bars.map(bar => ({
    date: bar.date,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume,
  }))
  const before = zoom.data?.bars_before ?? 0
  const after = zoom.data?.bars_after ?? 0
  const shortAfter = Boolean(zoom.data && asOf && zoom.data.to_date > asOf && after < 200)
  const gainText = formatGain(row.gain)
  const caption = zoom.isLoading
    ? '正在读取起涨前一年到结束后一年…'
    : zoom.isError
      ? '前后一年的 K 线没有读出来。'
      : `放大图 ${zoom.data?.from_date} ~ ${bars[bars.length - 1]?.date ?? zoom.data?.to_date}，前 ${before} 根，后 ${after} 根。${frame === 'run' ? '当前对准这段上涨。' : '当前是全年，红色区间是这段上涨。'}${shortAfter ? `一年要到 ${zoom.data?.to_date}，最新行情只到 ${asOf}。` : ''}`
  const markers = row.start_date === row.end_date
    ? [{ date: row.start_date, kind: 'neutral' as const, above: true, label: gainText, color: '#FACC15' }]
    : [
        { date: row.start_date, kind: 'neutral' as const, above: true, label: '起涨', color: '#FACC15' },
        { date: row.end_date, kind: 'neutral' as const, above: true, label: gainText, color: '#FACC15' },
      ]
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" onMouseDown={backdrop.onMouseDown} onClick={backdrop.onClick}>
      <div className="flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-xl" onMouseDown={event => event.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-foreground">{row.name || row.symbol} · {row.symbol}</div>
            <div className="mt-0.5 text-xs text-muted">
              {formatSpan(row)} · {gainText} · {row.days} 天 · 涨前收盘 {row.base_close.toFixed(2)} → 结束收盘 {row.end_close.toFixed(2)} · {caption}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button type="button" className={`h-8 rounded-btn px-2 text-xs ${frame === 'run' ? 'bg-elevated text-foreground' : 'text-muted hover:text-foreground'}`} onClick={() => setFrame('run')}>看这段</button>
            <button type="button" className={`h-8 rounded-btn px-2 text-xs ${frame === 'year' ? 'bg-elevated text-foreground' : 'text-muted hover:text-foreground'}`} onClick={() => setFrame('year')}>看全年</button>
            <button type="button" className="inline-flex h-8 items-center gap-1 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => nav.go(-1)} disabled={!nav.navEnabled}>
              <ChevronLeft className="h-3.5 w-3.5" />上一条
            </button>
            <button type="button" className="inline-flex h-8 items-center gap-1 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => nav.go(1)} disabled={!nav.navEnabled}>
              下一条<ChevronRight className="h-3.5 w-3.5" />
            </button>
            <button type="button" className="grid h-8 w-8 place-items-center rounded-btn text-muted hover:bg-elevated hover:text-foreground" onClick={onClose} aria-label="关闭">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="min-h-0 overflow-auto p-3">
          {zoom.isLoading ? (
            <div className="grid h-[520px] place-items-center text-sm text-muted">正在读取前后一年的 K 线…</div>
          ) : zoom.isError ? (
            <ErrorState hint="前后一年的日线没有读出来。" onRetry={() => zoom.refetch()} retrying={zoom.isRefetching} />
          ) : data.length === 0 ? (
            <EmptyState title="没有可放大的 K 线" hint="这段上涨前后一年的日线没有读到。" />
          ) : (
            <EChartsCandlestick
              key={`${row.id}|${frame}`}
              data={data}
              height={520}
              visibleBars="all"
              focus={focus}
              showMA
              ranges={[{ start: row.start_date, end: row.end_date, label: gainText, color: 'rgba(199,64,64,0.42)' }]}
              markers={markers}
              stockInfo={{ name: row.name }}
              symbol={row.symbol}
            />
          )}
          {nav.wrapMsg && <div className="mt-2 text-center text-xs text-muted">{nav.wrapMsg}</div>}
        </div>
      </div>
    </div>
  )
}


const SELECTED_BAR = '#E2B15A'

function StartCountChart({
  start,
  end,
  minGain,
  fullWindow,
  refreshing,
  bucket,
  selected,
  onBucket,
  onSelect,
}: {
  start: string
  end: string
  minGain: number
  fullWindow: boolean
  refreshing: boolean
  bucket: 'day' | 'month'
  selected: string
  onBucket: (bucket: 'day' | 'month') => void
  onSelect: (value: string) => void
}) {
  const ct = useChartTheme()
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  const selectRef = useRef(onSelect)
  selectRef.current = onSelect
  const query = useQuery({
    queryKey: QK.riseResearch(`starts|${start}|${end}|${minGain}|${fullWindow ? 1 : 0}|${bucket}`),
    queryFn: () => api.riseResearchStarts({
      start: start || undefined,
      end: end || undefined,
      minGain,
      fullWindow,
      bucket,
    }),
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
          const raw = row.value ?? row.data
          const count = typeof raw === 'number' ? raw : (raw && typeof raw === 'object' && 'value' in raw && typeof raw.value === 'number' ? raw.value : 0)
          return `${row.axisValue}<br/>启动 ${count} 段`
        },
      },
      xAxis: {
        type: 'category',
        data: points.map(point => point.date),
        axisLabel: { color: ct.text, fontSize: 10, hideOverlap: true },
        axisLine: { lineStyle: { color: ct.border } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        axisLabel: { color: ct.text, fontSize: 10 },
        splitLine: { lineStyle: { color: ct.grid } },
      },
      dataZoom: bucket === 'day' ? [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 8 }] : [],
      series: [{
        type: 'bar',
        cursor: 'pointer',
        barMaxWidth: bucket === 'month' ? 28 : 12,
        data: points.map(point => ({
          value: point.count,
          itemStyle: { color: point.date === selected ? SELECTED_BAR : BULL },
        })),
      }],
    }, true)
    inst.off('click')
    inst.on('click', (params: { componentType?: string; name?: string }) => {
      if (params.componentType === 'series' && params.name) selectRef.current(params.name)
    })
    const onResize = () => inst.resize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [points, bucket, ct, selected])
  useEffect(() => () => {
    chart.current?.dispose()
    chart.current = null
  }, [])
  const total = points.reduce((sum, point) => sum + point.count, 0)
  return (
    <div className="shrink-0 border-b border-border px-5 py-2">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-xs text-secondary">连涨启动个数</span>
        <button type="button" className={`h-7 rounded-btn px-2 text-xs ${bucket === 'day' ? 'bg-elevated text-foreground' : 'text-muted'}`} onClick={() => onBucket('day')}>按日</button>
        <button type="button" className={`h-7 rounded-btn px-2 text-xs ${bucket === 'month' ? 'bg-elevated text-foreground' : 'text-muted'}`} onClick={() => onBucket('month')}>按月</button>
        {selected && (
          <button type="button" className="h-7 rounded-btn px-2 text-xs text-secondary hover:text-foreground" onClick={() => onSelect('')}>
            清除选择 {selected}
          </button>
        )}
        {query.data && <span className="text-[11px] text-muted">合计 {total} 段</span>}
      </div>
      {(query.isLoading || refreshing) ? (
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

function RiseResearchPage() {
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [minGain, setMinGain] = useState(0.3)
  const [sort, setSort] = useState<'end_date' | 'gain' | 'days'>('end_date')
  const [order, setOrder] = useState<'desc' | 'asc'>('desc')
  const [draftQ, setDraftQ] = useState('')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [fullWindow, setFullWindow] = useState(true)
  const [bucket, setBucket] = useState<'day' | 'month'>('day')
  const [started, setStarted] = useState('')

  useEffect(() => {
    const timer = window.setTimeout(() => setQ(draftQ.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [draftQ])

  useEffect(() => {
    setOffset(0)
  }, [start, end, minGain, sort, order, q, fullWindow, started])

  const queryKey = `${start}|${end}|${minGain}|${sort}|${order}|${q}|${offset}|${fullWindow ? 1 : 0}|${started}`
  const query = useQuery({
    queryKey: QK.riseResearch(queryKey),
    queryFn: () => api.riseResearch({
      start: start || undefined,
      end: end || undefined,
      minGain,
      q,
      sort,
      order,
      offset,
      limit: PAGE_SIZE,
      fullWindow,
      started: started || undefined,
    }),
  })

  const queryClient = useQueryClient()
  const rebuild = useMutation({
    mutationFn: () => api.riseResearchRebuild(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['rise-research'] }),
  })

  const rows = query.data?.rows ?? []
  const total = query.data?.total ?? 0
  const page = Math.floor(offset / PAGE_SIZE) + 1
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const selected = useMemo(
    () => rows.find(row => row.id === selectedId) ?? null,
    [rows, selectedId],
  )

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="连涨研究"
        subtitle={query.data?.as_of
          ? `截至 ${query.data.as_of}。从低点走到高点算一段，中间可以下跌。最高收盘比起点高出 30% 以上才留下；从最高收盘回落超过 5% 时，这段记到最高那天。缩略图是起点前 ${query.data.bars_before} 个交易日到高点后 ${query.data.bars_after} 个交易日。${fullWindow ? `当前只看高点不晚于 ${query.data.window_cutoff || '窗口截止日'} 的记录，后面的 K 线已经走完。` : '包含还没走完 30 个交易日的上涨段。'}`
          : '从低点走到高点，中间可以下跌，累计涨幅大于 30%。'}
      />
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-2">
        <DatePicker value={start} onChange={setStart} max={end || undefined} placeholder="开始日期" align="left" />
        <DatePicker value={end} onChange={setEnd} min={start || undefined} placeholder="结束日期" align="left" />
        {(start || end) && (
          <button type="button" className="h-8 rounded-btn px-2 text-xs text-muted hover:bg-elevated hover:text-foreground" onClick={() => { setStart(''); setEnd('') }}>清空日期</button>
        )}
        <label className="flex items-center gap-1 text-xs text-muted">
          最低涨幅
          <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={minGain} onChange={event => setMinGain(Number(event.target.value))}>
            <option value={0.3}>大于 30%</option>
            <option value={0.5}>大于 50%</option>
            <option value={1}>大于 100%</option>
          </select>
        </label>
        <label className="flex items-center gap-1 text-xs text-muted">
          排序
          <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={sort} onChange={event => setSort(event.target.value as 'end_date' | 'gain' | 'days')}>
            <option value="end_date">结束日</option>
            <option value="gain">涨幅</option>
            <option value="days">天数</option>
          </select>
        </label>
        <button type="button" className="h-8 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => setOrder(value => value === 'desc' ? 'asc' : 'desc')}>
          {order === 'desc' ? '降序' : '升序'}
        </button>
        <button type="button" className="h-8 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => setFullWindow(value => !value)}>
          {fullWindow ? '已满30日' : '包含最新'}
        </button>
        <button
          type="button"
          className="h-8 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground disabled:opacity-40"
          disabled={rebuild.isPending}
          onClick={() => rebuild.mutate()}
        >
          {rebuild.isPending ? '正在重新划分…' : '重新计算'}
        </button>
        <label className="relative ml-auto">
          <Search className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted" />
          <input
            value={draftQ}
            onChange={event => setDraftQ(event.target.value)}
            placeholder="代码或名称"
            className="h-8 w-40 rounded-btn bg-elevated pl-7 pr-2 text-xs text-foreground outline-none"
          />
        </label>
      </div>
      <StartCountChart
        start={start}
        end={end}
        minGain={minGain}
        fullWindow={fullWindow}
        refreshing={rebuild.isPending}
        bucket={bucket}
        selected={started}
        onBucket={next => { setBucket(next); setStarted('') }}
        onSelect={value => setStarted(current => current === value ? '' : value)}
      />
      <div className="min-h-0 flex-1 overflow-auto px-5 py-3">
        {(query.isLoading || rebuild.isPending) ? (
          <div className="py-16 text-center text-sm text-muted">{rebuild.isPending ? '正在重新划分上涨段…' : '正在读取已保存的结果…'}</div>
        ) : query.isError ? (
          <ErrorState hint="上涨段或 K 线没有读出来。" onRetry={() => query.refetch()} retrying={query.isRefetching} />
        ) : query.data?.computed === false ? (
          <EmptyState icon={Search} title="还没有计算，请点重新计算" hint="算完会保存下来。之后打开页面直接读取，不会因为行情更新自动重算。" />
        ) : rows.length === 0 ? (
          <EmptyState icon={Search} title={started ? `${started} 没有启动的上涨段` : '这个条件下没有上涨段'} hint={started ? '再点一次这根柱子，或点清除选择。' : '放宽日期或涨幅后再看。从高点回落不超过 5% 的下跌仍算在同一段里。'} />
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {rows.map(row => (
              <button
                key={row.id}
                type="button"
                onClick={() => setSelectedId(row.id)}
                className="rounded-lg border border-border bg-surface p-3 text-left transition-colors hover:border-accent/50"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <div className="min-w-0 truncate text-sm font-medium text-foreground">{row.name || row.symbol}</div>
                  <div className="shrink-0 text-sm text-bull">{formatGain(row.gain)} · {row.days} 天</div>
                </div>
                <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-muted">
                  <span className="truncate font-mono">{row.symbol}</span>
                  <span>{formatSpan(row)}</span>
                </div>
                <div className="mt-1 text-[11px] text-muted">涨前 {row.base_close.toFixed(2)} → 结束 {row.end_close.toFixed(2)} · 前 {barSides(row.bars, row.start_date, row.end_date).before} 根 · 后 {barSides(row.bars, row.start_date, row.end_date).after} 根</div>
                <div className="mt-2">
                  <MiniRunChart bars={row.bars} start={row.start_date} end={row.end_date} />
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="flex items-center justify-between border-t border-border px-5 py-2 text-xs text-muted">
        <span>共 {total} 条 · 第 {page} / {pages} 页</span>
        <div className="flex items-center gap-2">
          <button type="button" className="h-8 rounded-btn bg-elevated px-2 disabled:opacity-40" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>上一页</button>
          <button type="button" className="h-8 rounded-btn bg-elevated px-2 disabled:opacity-40" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(value => value + PAGE_SIZE)}>下一页</button>
        </div>
      </div>
      {selected && (
        <RunDialog
          rows={rows}
          currentId={selected.id}
          asOf={query.data?.as_of ?? ''}
          onSelect={setSelectedId}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  )
}

const extension: FrontendExtension = {
  id: 'official.rise-research',
  apiVersion: 1,
  routes: [
    { id: 'rise-research', path: '/rise-research', component: RiseResearchPage },
  ],
  navigation: [
    { id: 'rise-research', routeId: 'rise-research', label: '连涨研究', icon: IconRiseResearch },
  ],
}

export default extension
