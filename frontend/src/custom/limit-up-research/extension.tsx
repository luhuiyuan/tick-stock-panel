import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Search, X } from 'lucide-react'
import { DatePicker } from '@/components/DatePicker'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { PageHeader } from '@/components/PageHeader'
import { IconLimitResearch } from '@/components/BrandIcons'
import { api, type LimitUpResearchBar, type LimitUpResearchStreak } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { useListNav } from '@/lib/useListNav'
import { useDialogBackdrop } from '@/lib/useDialogBackdrop'
import type { FrontendExtension } from '@/extensions/types'

const PAGE_SIZE = 24
const BULL = '#C74040'
const BEAR = '#2D9B65'
const AFTER = 'rgba(45,155,101,0.16)'

function streakKey(row: LimitUpResearchStreak) {
  return row.id
}

function formatSpan(row: LimitUpResearchStreak) {
  return row.start_date === row.end_date ? row.start_date : `${row.start_date} ~ ${row.end_date}`
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

function MiniStreakChart({ bars, start, end }: { bars: LimitUpResearchBar[]; start: string; end: string }) {
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
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} className="block" role="img" aria-label="连板缩略K线">
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

function StreakDialog({
  rows,
  currentId,
  asOf,
  onSelect,
  onClose,
}: {
  rows: LimitUpResearchStreak[]
  currentId: string
  asOf: string
  onSelect: (id: string) => void
  onClose: () => void
}) {
  const backdrop = useDialogBackdrop(onClose)
  const nav = useListNav({
    items: rows,
    keyOf: streakKey,
    currentKey: currentId,
    onNavigate: row => onSelect(row.id),
    wrapHints: { head: '已到本页第一条', tail: '已到本页最后一条' },
  })
  const row = rows.find(item => item.id === currentId) ?? null
  const [frame, setFrame] = useState<'streak' | 'year'>('streak')
  useEffect(() => { setFrame('streak') }, [currentId])
  const focus = useMemo(() => {
    if (!row || frame === 'year') return 'all' as const
    return { start: row.start_date, end: row.end_date, padding: 20 }
  }, [frame, row])
  const zoom = useQuery({
    queryKey: QK.limitUpResearch(`zoom|${currentId}`),
    enabled: row != null,
    queryFn: () => api.limitUpResearchZoom({
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
  const caption = zoom.isLoading
    ? '正在读取首板前一年到结束后一年…'
    : zoom.isError
      ? '前后一年的 K 线没有读出来。'
      : `放大图 ${zoom.data?.from_date} ~ ${bars[bars.length - 1]?.date ?? zoom.data?.to_date}，前 ${before} 根，后 ${after} 根。${frame === 'streak' ? '当前对准这段涨停。' : '当前是全年，红色区间是这段涨停。'}${shortAfter ? `一年要到 ${zoom.data?.to_date}，最新行情只到 ${asOf}。` : ''}`
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" onMouseDown={backdrop.onMouseDown} onClick={backdrop.onClick}>
      <div className="flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-xl" onMouseDown={event => event.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-foreground">{row.name || row.symbol} · {row.symbol}</div>
            <div className="mt-0.5 text-xs text-muted">{formatSpan(row)} · {row.boards} 连板 · {caption}</div>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" className={`h-8 rounded-btn px-2 text-xs ${frame === 'streak' ? 'bg-elevated text-foreground' : 'text-muted hover:text-foreground'}`} onClick={() => setFrame('streak')}>看连板</button>
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
            <EmptyState title="没有可放大的 K 线" hint="这段涨停前后一年的日线没有读到。" />
          ) : (
            <EChartsCandlestick
              key={row.id}
              data={data}
              height={520}
              visibleBars="all"
              focus={focus}
              showMA
              ranges={[{ start: row.start_date, end: row.end_date, label: `${row.boards}连板`, color: 'rgba(199,64,64,0.42)' }]}
              markers={row.start_date === row.end_date
                ? [{ date: row.start_date, kind: 'neutral', above: true, label: `${row.boards}连板`, color: '#FACC15' }]
                : [
                    { date: row.start_date, kind: 'neutral', above: true, label: '首板', color: '#FACC15' },
                    { date: row.end_date, kind: 'neutral', above: true, label: `${row.boards}连板`, color: '#FACC15' },
                  ]}
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

function LimitUpResearchPage() {
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [minBoards, setMinBoards] = useState(1)
  const [sort, setSort] = useState<'end_date' | 'boards'>('end_date')
  const [order, setOrder] = useState<'desc' | 'asc'>('desc')
  const [draftQ, setDraftQ] = useState('')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [fullWindow, setFullWindow] = useState(true)

  useEffect(() => {
    const timer = window.setTimeout(() => setQ(draftQ.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [draftQ])

  useEffect(() => {
    setOffset(0)
  }, [start, end, minBoards, sort, order, q, fullWindow])

  const queryKey = `${start}|${end}|${minBoards}|${sort}|${order}|${q}|${offset}|${fullWindow ? 1 : 0}`
  const query = useQuery({
    queryKey: QK.limitUpResearch(queryKey),
    queryFn: () => api.limitUpResearch({
      start: start || undefined,
      end: end || undefined,
      minBoards,
      q,
      sort,
      order,
      offset,
      limit: PAGE_SIZE,
      fullWindow,
    }),
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
        title="涨停研究"
        subtitle={query.data?.as_of
          ? `截至 ${query.data.as_of}。缩略图是首板前 ${query.data.bars_before} 个交易日到结束后 ${query.data.bars_after} 个交易日。${fullWindow ? `当前只看结束日不晚于 ${query.data.window_cutoff || '窗口截止日'} 的记录，后面的 K 线已经走完。` : '包含刚结束的连板；这些记录后面还没有 K 线。'}`
          : '一段连续涨停算一条。'}
      />
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-2">
        <DatePicker value={start} onChange={setStart} max={end || undefined} placeholder="开始日期" align="left" />
        <DatePicker value={end} onChange={setEnd} min={start || undefined} placeholder="结束日期" align="left" />
        {(start || end) && (
          <button type="button" className="h-8 rounded-btn px-2 text-xs text-muted hover:bg-elevated hover:text-foreground" onClick={() => { setStart(''); setEnd('') }}>清空日期</button>
        )}
        <label className="flex items-center gap-1 text-xs text-muted">
          最少连板
          <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={minBoards} onChange={event => setMinBoards(Number(event.target.value))}>
            {[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1 text-xs text-muted">
          排序
          <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={sort} onChange={event => setSort(event.target.value as 'end_date' | 'boards')}>
            <option value="end_date">结束日</option>
            <option value="boards">连板数</option>
          </select>
        </label>
        <button type="button" className="h-8 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => setOrder(value => value === 'desc' ? 'asc' : 'desc')}>
          {order === 'desc' ? '降序' : '升序'}
        </button>
        <button type="button" className="h-8 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => setFullWindow(value => !value)}>
          {fullWindow ? '已满30日' : '包含最新'}
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
      <div className="min-h-0 flex-1 overflow-auto px-5 py-3">
        {query.isLoading ? (
          <div className="py-16 text-center text-sm text-muted">正在整理连板记录…</div>
        ) : query.isError ? (
          <ErrorState hint="涨停索引或 K 线没有读出来。" onRetry={() => query.refetch()} retrying={query.isRefetching} />
        ) : rows.length === 0 ? (
          <EmptyState icon={Search} title="这个条件下没有连板记录" hint="放宽日期或连板数后再看。只涨停一天也会单独成一条。" />
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
                  <div className="shrink-0 text-sm text-bull">{row.boards} 连板</div>
                </div>
                <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-muted">
                  <span className="truncate font-mono">{row.symbol}</span>
                  <span>{formatSpan(row)}</span>
                </div>
                {row.days !== row.boards && (
                  <div className="mt-1 text-[11px] text-muted">区间内看到 {row.days} 个涨停日，连板数是 {row.boards}</div>
                )}
                <div className="mt-1 text-[11px] text-muted">前 {barSides(row.bars, row.start_date, row.end_date).before} 根 · 后 {barSides(row.bars, row.start_date, row.end_date).after} 根</div>
                <div className="mt-2">
                  <MiniStreakChart bars={row.bars} start={row.start_date} end={row.end_date} />
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
        <StreakDialog
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
  id: 'official.limit-up-research',
  apiVersion: 1,
  routes: [
    { id: 'limit-up-research', path: '/limit-up-research', component: LimitUpResearchPage },
  ],
  navigation: [
    { id: 'limit-up-research', routeId: 'limit-up-research', label: '涨停研究', icon: IconLimitResearch },
  ],
}

export default extension
