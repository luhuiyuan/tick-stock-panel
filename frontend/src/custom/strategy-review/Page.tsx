import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Search, X, Play } from 'lucide-react'
import { DatePicker } from '@/components/DatePicker'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { PageHeader } from '@/components/PageHeader'
import { api, type StrategyReviewBar, type StrategyReviewRun } from '@/lib/api'
import { useDialogBackdrop } from '@/lib/useDialogBackdrop'

const PAGE_SIZE = 24
const BULL = '#C74040'
const BEAR = '#2D9B65'
const AFTER = 'rgba(45,155,101,0.16)'
const HORIZONS = [1, 3, 5] as const
type Horizon = typeof HORIZONS[number]

function fmtPct(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  const pct = value * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`
}

function pick(row: StrategyReviewRun, horizon: number, field: 'ret' | 'status' | 'exit_date') {
  return row[`${field}_${horizon}` as keyof StrategyReviewRun]
}

function MiniSignalChart({ bars, signalDate, exitDate }: {
  bars: StrategyReviewBar[]
  signalDate: string
  exitDate: string | null
}) {
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
  const signalIdx = bars.findIndex(bar => bar.date >= signalDate)
  const exitIdx = exitDate ? bars.findIndex(bar => bar.date >= exitDate) : -1
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} className="block" role="img" aria-label="选股信号缩略K线">
      {signalIdx >= 0 && <rect x={signalIdx * barW} y={0} width={(bars.length - signalIdx) * barW} height={height} fill={AFTER} />}
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
      {signalIdx >= 0 && (
        <line x1={signalIdx * barW + barW / 2} x2={signalIdx * barW + barW / 2} y1={0} y2={height} stroke="#E2B15A" strokeWidth={1.5} strokeDasharray="3 2" />
      )}
      {exitIdx >= 0 && (
        <line x1={exitIdx * barW + barW / 2} x2={exitIdx * barW + barW / 2} y1={0} y2={height} stroke="#8A8A8A" strokeWidth={1} strokeDasharray="2 2" />
      )}
    </svg>
  )
}

function SignalDialog({ rows, currentId, horizon, onSelect, onClose }: {
  rows: StrategyReviewRun[]
  currentId: string
  horizon: number
  onSelect: (id: string) => void
  onClose: () => void
}) {
  const backdrop = useDialogBackdrop(onClose)
  const index = rows.findIndex(row => row.id === currentId)
  const row = rows[index] ?? null
  const zoom = useQuery({
    queryKey: ['strategy-review', 'zoom', currentId],
    enabled: row != null,
    queryFn: () => api.strategyReviewZoom({
      symbol: row?.symbol ?? '',
      start: row?.signal_date ?? '',
      end: row?.signal_date ?? '',
    }),
  })
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  if (!row) return null
  const bars = zoom.data?.bars ?? []
  const data = bars.map(bar => ({
    date: bar.date, open: bar.open, high: bar.high, low: bar.low,
    close: bar.close, volume: bar.volume,
  }))
  const exitDate = pick(row, horizon, 'exit_date') as string | null
  const ret = pick(row, horizon, 'ret') as number | null
  const markers = [
    { date: row.signal_date, kind: 'neutral' as const, above: true, label: '买入', color: '#E2B15A' },
    ...(exitDate ? [{ date: exitDate, kind: 'neutral' as const, above: true, label: `卖出 ${fmtPct(ret)}`, color: '#9CA3AF' }] : []),
  ]
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" onMouseDown={backdrop.onMouseDown} onClick={backdrop.onClick}>
      <div className="flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-xl" onMouseDown={event => event.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-foreground">{row.name || row.symbol} · {row.symbol}</div>
            <div className="mt-0.5 text-xs text-muted">
              信号日 {row.signal_date} · 买入收盘 {row.entry_close?.toFixed(2) ?? '—'} · 持有 {horizon} 日 {fmtPct(ret)} · 命中 {row.strategy_count} 个策略{row.tradable === 'locked' ? ' · 信号日一字板/停牌' : ''}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button type="button" className="inline-flex h-8 items-center gap-1 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => { if (index > 0) onSelect(rows[index - 1].id) }} disabled={index <= 0}>
              <ChevronLeft className="h-3.5 w-3.5" />上一条
            </button>
            <button type="button" className="inline-flex h-8 items-center gap-1 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => { if (index >= 0 && index < rows.length - 1) onSelect(rows[index + 1].id) }} disabled={index < 0 || index >= rows.length - 1}>
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
            <EmptyState title="没有可放大的 K 线" hint="这只股票在信号日前后一年的日线没有读到。" />
          ) : (
            <EChartsCandlestick
              key={`${row.id}|${horizon}`}
              data={data}
              height={520}
              visibleBars="all"
              focus={{ start: row.signal_date, end: exitDate ?? row.signal_date, padding: 20 }}
              showMA
              ranges={exitDate ? [{ start: row.signal_date, end: exitDate, label: `持有 ${horizon} 日 ${fmtPct(ret)}`, color: 'rgba(226,177,90,0.24)' }] : []}
              markers={markers}
              stockInfo={{ name: row.name }}
              symbol={row.symbol}
            />
          )}
        </div>
      </div>
    </div>
  )
}

function StatsCards({ stats, horizon, onHorizon }: {
  stats: Record<string, { win_rate: number | null; avg_ret: number | null; median_ret: number | null; win: number; lose: number; pending: number; total: number; profit_loss_ratio: number | null }>
  horizon: number
  onHorizon: (h: Horizon) => void
}) {
  return (
    <div className="grid grid-cols-1 gap-2 border-b border-border px-5 py-2 sm:grid-cols-3">
      {HORIZONS.map(h => {
        const stat = stats[String(h)]
        const active = horizon === h
        return (
          <button
            key={h}
            type="button"
            onClick={() => onHorizon(h)}
            className={`rounded-lg border px-3 py-2 text-left transition-colors ${active ? 'border-accent/60 bg-elevated' : 'border-border bg-surface hover:border-accent/30'}`}
          >
            <div className="flex items-baseline justify-between">
              <span className="text-xs text-secondary">持有 {h} 个交易日{active ? ' · 当前' : ''}</span>
              <span className={`text-lg font-semibold ${stat?.win_rate === null || stat?.win_rate === undefined ? 'text-muted' : stat.win_rate >= 0.5 ? 'text-bull' : 'text-bear'}`}>
                {stat?.win_rate != null ? `${(stat.win_rate * 100).toFixed(1)}%` : '—'}
              </span>
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted">
              <span>平均 <b className={stat?.avg_ret != null && stat.avg_ret >= 0 ? 'text-bull' : 'text-bear'}>{fmtPct(stat?.avg_ret)}</b></span>
              <span>中位 <b className={stat?.median_ret != null && stat.median_ret >= 0 ? 'text-bull' : 'text-bear'}>{fmtPct(stat?.median_ret)}</b></span>
              <span>盈亏比 {stat?.profit_loss_ratio != null ? stat.profit_loss_ratio.toFixed(2) : '—'}</span>
            </div>
            <div className="mt-0.5 text-[11px] text-muted">
              {stat?.win ?? 0} 赢 / {stat?.lose ?? 0} 输 / {stat?.pending ?? 0} 观察中 · 有效样本 {stat?.total ?? 0}
            </div>
          </button>
        )
      })}
    </div>
  )
}

export function StrategyReviewPage() {
  const [strategyIds, setStrategyIds] = useState<string[]>([])
  const [showStrategies, setShowStrategies] = useState(false)
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [costPct, setCostPct] = useState('0.15')
  const [horizon, setHorizon] = useState<Horizon>(1)
  const [result, setResult] = useState<'all' | 'win' | 'lose' | 'pending'>('all')
  const [strategyId, setStrategyId] = useState('')
  const [minRet, setMinRet] = useState('')
  const [maxRet, setMaxRet] = useState('')
  const [draftQ, setDraftQ] = useState('')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<'signal_date' | 'ret_1' | 'ret_3' | 'ret_5' | 'strategy_count'>('signal_date')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [offset, setOffset] = useState(0)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobDone, setJobDone] = useState(false)

  const queryClient = useQueryClient()
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(draftQ.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [draftQ])
  useEffect(() => { setOffset(0) }, [horizon, result, start, end, q, strategyId, minRet, maxRet, sort, order])

  const strategiesQuery = useQuery({
    queryKey: ['strategy-review', 'strategies'],
    queryFn: () => api.strategyReviewStrategies(),
    staleTime: 5 * 60_000,
  })
  const presets = (strategiesQuery.data?.presets ?? []).filter(p => p.execution_available)

  const rebuild = useMutation({
    mutationFn: () => api.strategyReviewRebuild({
      strategyIds,
      start: start || undefined,
      end: end || undefined,
      costPct: Number(costPct) / 100,
    }),
    onSuccess: data => { setJobId(data.job_id); setJobDone(false); setOffset(0) },
  })

  const jobQuery = useQuery({
    queryKey: ['strategy-review', 'job', jobId],
    queryFn: () => api.strategyReviewJob(jobId!),
    enabled: Boolean(jobId) && !jobDone,
    refetchInterval: 1200,
  })
  useEffect(() => {
    const status = jobQuery.data?.status
    if (status === 'succeeded' || status === 'failed') {
      setJobDone(true)
      queryClient.invalidateQueries({ queryKey: ['strategy-review'] })
    }
  }, [jobQuery.data?.status, queryClient])

  const key = ['runs', horizon, result, start, end, q, strategyId, minRet, maxRet, sort, order, offset].join('|')
  const runsQuery = useQuery({
    queryKey: ['strategy-review', 'runs', key],
    queryFn: () => api.strategyReviewRuns({
      horizon, result,
      start: start || undefined, end: end || undefined,
      q, strategyId: strategyId || undefined,
      minRet: minRet === '' ? undefined : Number(minRet) / 100,
      maxRet: maxRet === '' ? undefined : Number(maxRet) / 100,
      sort, order, offset, limit: PAGE_SIZE,
    }),
  })

  const rows = runsQuery.data?.rows ?? []
  const total = runsQuery.data?.total ?? 0
  const page = Math.floor(offset / PAGE_SIZE) + 1
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const stats = runsQuery.data?.stats ?? {}
  const meta = runsQuery.data?.meta ?? {}
  const running = Boolean(jobId) && !jobDone
  const lastLog = jobQuery.data?.log?.[jobQuery.data.log.length - 1]?.msg ?? ''

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="策略复盘"
        subtitle={meta.start
          ? `区间 ${meta.start} ~ ${meta.end} · ${meta.strategy_ids?.length ?? 0} 个策略 · ${meta.scan_days ?? 0} 个交易日 · 命中 ${meta.signals ?? 0} 条。买入按信号日收盘，卖出按第 1/3/5 个交易日收盘，扣双边成本 ${((meta.cost_pct ?? 0.0015) * 100).toFixed(2)}%。缩略图前 ${runsQuery.data?.bars_before ?? 20} 根后 ${runsQuery.data?.bars_after ?? 10} 根。结果使用信号日之后的行情计算，属于历史复盘，不构成实盘信号。`
          : '指定策略在历史区间逐日选股，看 K 线缩略图与后续 1/3/5 日盈利统计。不填日期默认最近 120 个交易日。结果含未来数据，仅用于复盘。'}
      />

      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-2">
        <button
          type="button"
          className="h-8 rounded-btn bg-elevated px-2.5 text-xs text-secondary hover:text-foreground"
          onClick={() => setShowStrategies(value => !value)}
        >
          策略 · 已选 {strategyIds.length} 个 {showStrategies ? '▲' : '▼'}
        </button>
        <DatePicker value={start} onChange={setStart} max={end || undefined} placeholder="开始日期" align="left" />
        <DatePicker value={end} onChange={setEnd} min={start || undefined} placeholder="结束日期" align="left" />
        {(start || end) && (
          <button type="button" className="h-8 rounded-btn px-2 text-xs text-muted hover:bg-elevated hover:text-foreground" onClick={() => { setStart(''); setEnd('') }}>清空日期</button>
        )}
        <label className="flex items-center gap-1 text-xs text-muted">
          成本%
          <input type="number" step="0.01" min="0" max="1" value={costPct} onChange={e => setCostPct(e.target.value)} className="h-8 w-16 rounded-btn bg-elevated px-2 text-xs text-foreground outline-none" />
        </label>
        <button
          type="button"
          className="inline-flex h-8 items-center gap-1 rounded-btn bg-accent px-3 text-xs font-medium text-white disabled:opacity-40"
          disabled={strategyIds.length === 0 || running || rebuild.isPending}
          onClick={() => rebuild.mutate()}
        >
          <Play className="h-3.5 w-3.5" />{running || rebuild.isPending ? '正在扫描…' : '运行选股'}
        </button>
        {strategiesQuery.isError && <span className="text-xs text-danger">策略清单读取失败</span>}
      </div>

      {showStrategies && (
        <div className="border-b border-border bg-surface/60 px-5 py-2">
          <div className="mb-2 flex items-center gap-2 text-xs text-muted">
            <span>只列出可执行的日线选股策略（共 {presets.length} 个）。建议一次选 2-5 个、区间不超过 60 个交易日；一次全选可能因个别策略计算过慢而长时间无响应。</span>
            <button type="button" className="h-7 rounded-btn bg-elevated px-2 hover:text-foreground" onClick={() => setStrategyIds(presets.map(p => p.id))}>全选</button>
            <button type="button" className="h-7 rounded-btn bg-elevated px-2 hover:text-foreground" onClick={() => setStrategyIds([])}>清空</button>
          </div>
          <div className="grid max-h-52 grid-cols-1 gap-1 overflow-auto sm:grid-cols-2 xl:grid-cols-3">
            {presets.map(preset => (
              <label key={preset.id} className="flex cursor-pointer items-center gap-1.5 rounded px-1 py-0.5 text-xs text-secondary hover:bg-elevated">
                <input
                  type="checkbox"
                  checked={strategyIds.includes(preset.id)}
                  onChange={event => setStrategyIds(current => event.target.checked ? [...current, preset.id] : current.filter(id => id !== preset.id))}
                />
                <span className="truncate" title={preset.description || preset.id}>{preset.name}</span>
                <span className="shrink-0 font-mono text-[10px] text-muted">{preset.id}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      {running && jobQuery.data && (
        <div className="border-b border-border px-5 py-2">
          <div className="flex items-center justify-between text-xs text-secondary">
            <span>{jobQuery.data.status === 'failed' ? '扫描失败' : jobQuery.data.stage === 'init' ? '等待开始…' : lastLog || '正在扫描…'}</span>
            <span>{jobQuery.data.progress}%</span>
          </div>
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-elevated">
            <div className="h-full bg-accent transition-all" style={{ width: `${jobQuery.data.progress}%` }} />
          </div>
          {jobQuery.data.status === 'failed' && <div className="mt-1 text-xs text-danger">{jobQuery.data.error}</div>}
        </div>
      )}
      {rebuild.isError && <div className="border-b border-border px-5 py-2 text-xs text-danger">提交失败：{String(rebuild.error)}</div>}

      <StatsCards stats={stats} horizon={horizon} onHorizon={h => { setHorizon(h); setOffset(0) }} />

      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-2">
        <label className="flex items-center gap-1 text-xs text-muted">
          结果
          <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={result} onChange={event => setResult(event.target.value as typeof result)}>
            <option value="all">全部</option>
            <option value="win">盈利</option>
            <option value="lose">失败</option>
            <option value="pending">观察中</option>
          </select>
        </label>
        <label className="flex items-center gap-1 text-xs text-muted">
          策略
          <select className="h-8 max-w-[150px] rounded-btn bg-elevated px-2 text-foreground" value={strategyId} onChange={event => setStrategyId(event.target.value)}>
            <option value="">不限</option>
            {presets.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1 text-xs text-muted">
          收益% 从
          <input type="number" step="0.5" value={minRet} onChange={event => setMinRet(event.target.value)} placeholder="不限" className="h-8 w-16 rounded-btn bg-elevated px-2 text-xs text-foreground outline-none" />
          到
          <input type="number" step="0.5" value={maxRet} onChange={event => setMaxRet(event.target.value)} placeholder="不限" className="h-8 w-16 rounded-btn bg-elevated px-2 text-xs text-foreground outline-none" />
        </label>
        <label className="flex items-center gap-1 text-xs text-muted">
          排序
          <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={sort} onChange={event => setSort(event.target.value as typeof sort)}>
            <option value="signal_date">信号日</option>
            <option value="ret_1">1日收益</option>
            <option value="ret_3">3日收益</option>
            <option value="ret_5">5日收益</option>
            <option value="strategy_count">命中策略数</option>
          </select>
        </label>
        <button type="button" className="h-8 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => setOrder(value => value === 'desc' ? 'asc' : 'desc')}>
          {order === 'desc' ? '降序' : '升序'}
        </button>
        <label className="relative ml-auto">
          <Search className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted" />
          <input value={draftQ} onChange={event => setDraftQ(event.target.value)} placeholder="代码或名称" className="h-8 w-40 rounded-btn bg-elevated pl-7 pr-2 text-xs text-foreground outline-none" />
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-5 py-3">
        {runsQuery.isLoading ? (
          <div className="py-16 text-center text-sm text-muted">正在读取已保存的结果…</div>
        ) : runsQuery.isError ? (
          <ErrorState hint="选股结果没有读出来。" onRetry={() => runsQuery.refetch()} retrying={runsQuery.isRefetching} />
        ) : runsQuery.data?.computed === false ? (
          <EmptyState icon={Search} title="还没有运行过选股" hint="在上面选好策略与日期区间，点「运行选股」。算完会保存下来，之后打开页面直接读取。" />
        ) : rows.length === 0 ? (
          <EmptyState icon={Search} title="这个条件下没有信号" hint="放宽结果类型、收益区间或日期范围，或重新运行一次选股。" />
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {rows.map(row => {
              const ret = pick(row, horizon, 'ret') as number | null
              const status = pick(row, horizon, 'status') as string
              const exitDate = pick(row, horizon, 'exit_date') as string | null
              const badge = status === 'win' ? '盈利' : status === 'lose' ? '失败' : '观察中'
              return (
                <button
                  key={row.id}
                  type="button"
                  onClick={() => setSelectedId(row.id)}
                  className="rounded-lg border border-border bg-surface p-3 text-left transition-colors hover:border-accent/50"
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <div className="min-w-0 truncate text-sm font-medium text-foreground">{row.name || row.symbol}</div>
                    <div className={`shrink-0 text-sm ${ret === null ? 'text-muted' : ret >= 0 ? 'text-bull' : 'text-bear'}`}>{fmtPct(ret)} · {badge}</div>
                  </div>
                  <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-muted">
                    <span className="truncate font-mono">{row.symbol}</span>
                    <span>信号 {row.signal_date}</span>
                  </div>
                  <div className="mt-1 truncate text-[11px] text-muted">
                    买入 {row.entry_close?.toFixed(2) ?? '—'} · 卖出 {exitDate ?? '未走完'} · 命中 {row.strategy_count} 个策略{row.tradable === 'locked' ? ' · 一字板' : ''}
                  </div>
                  <div className="mt-0.5 truncate text-[10px] text-muted" title={row.strategy_ids.join(' / ')}>{row.strategy_ids.join(' / ')}</div>
                  <div className="mt-2">
                    <MiniSignalChart bars={row.bars} signalDate={row.signal_date} exitDate={exitDate} />
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-border px-5 py-2 text-xs text-muted">
        <span>共 {total} 条 · 第 {page} / {pages} 页 · 当前按持有 {horizon} 日</span>
        <div className="flex items-center gap-2">
          <button type="button" className="h-8 rounded-btn bg-elevated px-2 disabled:opacity-40" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>上一页</button>
          <button type="button" className="h-8 rounded-btn bg-elevated px-2 disabled:opacity-40" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(value => value + PAGE_SIZE)}>下一页</button>
        </div>
      </div>

      {selectedId && rows.length > 0 && (
        <SignalDialog rows={rows} currentId={selectedId} horizon={horizon} onSelect={setSelectedId} onClose={() => setSelectedId(null)} />
      )}
    </div>
  )
}
