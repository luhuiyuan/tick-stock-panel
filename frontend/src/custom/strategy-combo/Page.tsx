import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, Search, X, RefreshCw, Ban } from 'lucide-react'
import { DatePicker } from '@/components/DatePicker'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { PageHeader } from '@/components/PageHeader'
import {
  api,
  type StrategyComboRow,
  type StrategyComboStat,
  type StrategyReviewRun,
  type StrategyReviewBar,
} from '@/lib/api'
import { useDialogBackdrop } from '@/lib/useDialogBackdrop'

const HORIZONS = [1, 3, 5] as const
type Horizon = typeof HORIZONS[number]
type Tab = 'shortlist' | 'all' | 'heatmap' | 'coverage'

const BULL = '#C74040'
const BEAR = '#2D9B65'

function fmtPct(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  const pct = value * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`
}
function fmtWr(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`
}

/** 组合 label：把策略 id 替换成中文名，共振规则保持中文。 */
function formatLabel(row: StrategyComboRow, nameOf: Record<string, string>) {
  if (row.kind === 'resonance') return row.label
  return row.strategy_ids.map(s => nameOf[s] || s).join(' ∩ ')
}

/** 胜率→颜色：低=绿、中=灰、高=红（A 股语境：红=好），加图例说明。 */
function heatColor(wr: number | null, n: number) {
  if (wr === null || n === 0) return 'var(--color-elevated, #2a2a2a)'
  const t = Math.max(0, Math.min(1, (wr - 0.35) / 0.25))
  const mix = (a: number[], b: number[], f: number) => a.map((v, i) => Math.round(v + (b[i] - v) * f))
  const green = [45, 155, 101]
  const gray = [120, 120, 120]
  const red = [199, 64, 64]
  const c = t < 0.5 ? mix(green, gray, t / 0.5) : mix(gray, red, (t - 0.5) / 0.5)
  return `rgb(${c[0]},${c[1]},${c[2]})`
}

function StatBar({ stat, rankBy }: { stat: StrategyComboStat | null; rankBy: string }) {
  if (!stat) return <span className="text-muted">—</span>
  return (
    <div className="flex items-baseline gap-1.5">
      <span className={`font-semibold ${stat.win_rate >= 0.5 ? 'text-bull' : 'text-bear'}`}>{fmtWr(stat.win_rate)}</span>
      <span className={`text-[11px] ${stat.avg_ret >= 0 ? 'text-bull' : 'text-bear'}`}>{fmtPct(stat.avg_ret)}</span>
      <span className="text-[11px] text-muted">plr {stat.profit_loss_ratio.toFixed(2)}</span>
      {rankBy === 'composite' && <span className="text-[11px] text-muted">· 分 {stat.score.toFixed(3)}</span>}
    </div>
  )
}

function SegBars({ segWrs }: { segWrs: (number | null)[] }) {
  return (
    <div className="flex items-center gap-0.5" title={`分段胜率: ${segWrs.map(v => fmtWr(v)).join(' / ')}`}>
      {segWrs.map((v, i) => {
        const h = v === null ? 3 : Math.max(3, Math.round(v * 26))
        return <div key={i} style={{ height: h, background: v === null ? '#555' : v >= 0.5 ? BULL : BEAR }} className="w-2 rounded-sm" />
      })}
    </div>
  )
}

function MiniSignalChart({ bars, signalDate, exitDate }: {
  bars: StrategyReviewBar[]
  signalDate: string
  exitDate: string | null
}) {
  const width = 240
  const height = 72
  if (bars.length === 0) return <div className="grid h-[72px] place-items-center text-[11px] text-muted">无 K 线</div>
  let hi = -Infinity, lo = Infinity
  for (const b of bars) { if (b.high > hi) hi = b.high; if (b.low < lo) lo = b.low }
  const range = hi - lo || 1
  const barW = width / bars.length
  const y = (v: number) => 3 + (1 - (v - lo) / range) * (height - 6)
  const signalIdx = bars.findIndex(b => b.date >= signalDate)
  const exitIdx = exitDate ? bars.findIndex(b => b.date >= exitDate) : -1
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} className="block">
      {signalIdx >= 0 && <rect x={signalIdx * barW} y={0} width={(bars.length - signalIdx) * barW} height={height} fill="rgba(45,155,101,0.14)" />}
      {bars.map((bar, i) => {
        const color = bar.close >= bar.open ? BULL : BEAR
        const cx = i * barW + barW / 2
        const bodyW = Math.max(barW * 0.55, 1.2)
        const top = y(Math.max(bar.open, bar.close))
        const bottom = y(Math.min(bar.open, bar.close))
        return (
          <g key={bar.date}>
            <line x1={cx} x2={cx} y1={y(bar.high)} y2={y(bar.low)} stroke={color} strokeWidth={0.8} />
            <rect x={cx - bodyW / 2} y={top} width={bodyW} height={Math.max(bottom - top, 1)} fill={color} />
          </g>
        )
      })}
      {signalIdx >= 0 && <line x1={signalIdx * barW + barW / 2} x2={signalIdx * barW + barW / 2} y1={0} y2={height} stroke="#E2B15A" strokeWidth={1.2} strokeDasharray="3 2" />}
      {exitIdx >= 0 && <line x1={exitIdx * barW + barW / 2} x2={exitIdx * barW + barW / 2} y1={0} y2={height} stroke="#8A8A8A" strokeWidth={0.8} strokeDasharray="2 2" />}
    </svg>
  )
}

/** 下钻：组合命中的信号明细（分页 + 缩略图 + 放大弹窗）。 */
function ComboSignalsDialog({ comboKey, label, horizon, onClose }: {
  comboKey: string
  label: string
  horizon: number
  onClose: () => void
}) {
  const backdrop = useDialogBackdrop(onClose)
  const [result, setResult] = useState<'all' | 'win' | 'lose' | 'pending'>('all')
  const [offset, setOffset] = useState(0)
  const [zoom, setZoom] = useState<StrategyReviewRun | null>(null)
  const LIMIT = 24
  useEffect(() => { setOffset(0) }, [result])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { if (zoom) setZoom(null); else onClose() } }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, zoom])

  const signalsQuery = useQuery({
    queryKey: ['strategy-combo', 'signals', comboKey, horizon, result, offset],
    queryFn: () => api.strategyComboSignals({ key: comboKey, horizon, result, offset, limit: LIMIT }),
  })
  const rows = signalsQuery.data?.rows ?? []
  const total = signalsQuery.data?.total ?? 0
  const page = Math.floor(offset / LIMIT) + 1
  const pages = Math.max(1, Math.ceil(total / LIMIT))

  const zoomQuery = useQuery({
    queryKey: ['strategy-combo', 'zoom', zoom?.id],
    queryFn: () => api.strategyReviewZoom({
      symbol: zoom?.symbol ?? '', start: zoom?.signal_date ?? '', end: zoom?.signal_date ?? '',
    }),
    enabled: zoom != null,
  })

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" onMouseDown={backdrop.onMouseDown} onClick={backdrop.onClick}>
      <div className="flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-xl" onMouseDown={e => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-foreground">{label}</div>
            <div className="mt-0.5 text-xs text-muted">组合信号明细 · 持有 {horizon} 日 · 共 {total} 条</div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <select className="h-8 rounded-btn bg-elevated px-2 text-xs text-foreground" value={result} onChange={e => setResult(e.target.value as typeof result)}>
              <option value="all">全部</option><option value="win">盈利</option><option value="lose">失败</option><option value="pending">观察中</option>
            </select>
            <button type="button" className="grid h-8 w-8 place-items-center rounded-btn text-muted hover:bg-elevated hover:text-foreground" onClick={onClose} aria-label="关闭"><X className="h-4 w-4" /></button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-3">
          {signalsQuery.isLoading ? <div className="py-16 text-center text-sm text-muted">正在读取信号…</div>
            : signalsQuery.isError ? <ErrorState hint="信号明细没有读出来。" onRetry={() => signalsQuery.refetch()} retrying={signalsQuery.isRefetching} />
            : rows.length === 0 ? <EmptyState title="该条件下没有信号" hint="放宽结果类型或重新扫描信号池。" />
            : (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {rows.map(r => {
                  const ret = r[`ret_${horizon}` as keyof StrategyReviewRun] as number | null
                  const exitDate = r[`exit_date_${horizon}` as keyof StrategyReviewRun] as string | null
                  return (
                    <button key={r.id} type="button" onClick={() => setZoom(r)} className="rounded-lg border border-border bg-surface p-2 text-left hover:border-accent/50">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="truncate text-xs font-medium text-foreground">{r.name || r.symbol}</span>
                        <span className={`shrink-0 text-xs ${ret === null ? 'text-muted' : ret >= 0 ? 'text-bull' : 'text-bear'}`}>{fmtPct(ret)}</span>
                      </div>
                      <div className="mt-0.5 flex justify-between text-[10px] text-muted"><span className="font-mono">{r.symbol}</span><span>{r.signal_date}</span></div>
                      <div className="mt-1"><MiniSignalChart bars={r.bars} signalDate={r.signal_date} exitDate={exitDate} /></div>
                    </button>
                  )
                })}
              </div>
            )}
        </div>
        <div className="flex items-center justify-between border-t border-border px-4 py-2 text-xs text-muted">
          <span>第 {page} / {pages} 页</span>
          <div className="flex gap-2">
            <button type="button" className="h-7 rounded-btn bg-elevated px-2 disabled:opacity-40" disabled={offset === 0} onClick={() => setOffset(v => Math.max(0, v - LIMIT))}>上一页</button>
            <button type="button" className="h-7 rounded-btn bg-elevated px-2 disabled:opacity-40" disabled={offset + LIMIT >= total} onClick={() => setOffset(v => v + LIMIT)}>下一页</button>
          </div>
        </div>
      </div>

      {zoom && (
        <div className="fixed inset-0 z-[60] grid place-items-center bg-black/60 p-4" onMouseDown={e => e.stopPropagation()} onClick={() => setZoom(null)}>
          <div className="flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-xl" onMouseDown={e => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-border px-4 py-2">
              <div className="text-sm text-foreground">{zoom.name || zoom.symbol} · {zoom.symbol} · 信号 {zoom.signal_date}</div>
              <button type="button" className="grid h-8 w-8 place-items-center rounded-btn text-muted hover:bg-elevated" onClick={() => setZoom(null)}><X className="h-4 w-4" /></button>
            </div>
            <div className="min-h-0 overflow-auto p-3">
              {zoomQuery.isLoading ? <div className="grid h-[460px] place-items-center text-sm text-muted">读取前后一年 K 线…</div>
                : (zoomQuery.data?.bars?.length ?? 0) === 0 ? <EmptyState title="没有可放大的 K 线" hint="该股在信号日前后一年的日线没读到。" />
                : (
                  <EChartsCandlestick
                    data={zoomQuery.data!.bars.map(b => ({ date: b.date, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume }))}
                    height={460} visibleBars="all"
                    focus={{ start: zoom.signal_date, end: zoom.signal_date, padding: 20 }}
                    showMA
                    markers={[{ date: zoom.signal_date, kind: 'neutral', above: true, label: '买入', color: '#E2B15A' }]}
                    stockInfo={{ name: zoom.name }} symbol={zoom.symbol}
                  />
                )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export function StrategyComboPage() {
  const queryClient = useQueryClient()
  // 评估参数
  const [horizon, setHorizon] = useState<Horizon>(1)
  const [rankBy, setRankBy] = useState<'composite' | 'win_rate' | 'avg_ret'>('composite')
  const [minSamples, setMinSamples] = useState('50')
  const [topN, setTopN] = useState('10')
  const [isRatio, setIsRatio] = useState('0.70')
  const [maxComboSize, setMaxComboSize] = useState('3')
  const [tab, setTab] = useState<Tab>('shortlist')
  // 扫描参数
  const [strategyIds, setStrategyIds] = useState<string[]>([])
  const [showStrategies, setShowStrategies] = useState(false)
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [costPct, setCostPct] = useState('0.15')
  const [force, setForce] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobDone, setJobDone] = useState(true)
  // 下钻
  const [drill, setDrill] = useState<{ key: string; label: string } | null>(null)
  const [keyword, setKeyword] = useState('')

  const strategiesQuery = useQuery({
    queryKey: ['strategy-combo', 'strategies'],
    queryFn: () => api.strategyComboStrategies(),
    staleTime: 5 * 60_000,
  })
  const presets = useMemo(
    () => (strategiesQuery.data?.presets ?? []).filter(p => p.execution_available),
    [strategiesQuery.data],
  )
  const nameOf = useMemo(() => {
    const m: Record<string, string> = {}
    for (const p of strategiesQuery.data?.presets ?? []) m[p.id] = p.name
    return m
  }, [strategiesQuery.data])

  const metaQuery = useQuery({
    queryKey: ['strategy-combo', 'meta'],
    queryFn: () => api.strategyComboMeta(),
  })

  const scan = useMutation({
    mutationFn: () => api.strategyComboScan({
      strategyIds, start: start || undefined, end: end || undefined,
      costPct: Number(costPct) / 100, force,
    }),
    onSuccess: data => { setJobId(data.job_id); setJobDone(false) },
  })
  const jobQuery = useQuery({
    queryKey: ['strategy-combo', 'job', jobId],
    queryFn: () => api.strategyComboJob(jobId!),
    enabled: Boolean(jobId) && !jobDone,
    refetchInterval: 1200,
  })
  useEffect(() => {
    const status = jobQuery.data?.status
    if (status === 'succeeded' || status === 'failed') {
      setJobDone(true)
      queryClient.invalidateQueries({ queryKey: ['strategy-combo'] })
    }
  }, [jobQuery.data?.status, queryClient])
  const cancel = useMutation({
    mutationFn: () => api.strategyComboCancel(jobId!),
    onSuccess: () => setJobDone(true),
  })

  const params = {
    horizon, rankBy,
    minSamples: Number(minSamples) || 50,
    topN: Number(topN) || 10,
    isRatio: Number(isRatio) || 0.7,
    maxComboSize: Number(maxComboSize) || 3,
  }
  const rankKey = ['strategy-combo', 'rankings', params.horizon, params.rankBy, params.minSamples, params.topN, params.isRatio, params.maxComboSize].join('|')
  const rankingsQuery = useQuery({
    queryKey: ['strategy-combo', 'rankings', rankKey],
    queryFn: () => api.strategyComboRankings(params),
  })
  const heatmapQuery = useQuery({
    queryKey: ['strategy-combo', 'heatmap', horizon],
    queryFn: () => api.strategyComboHeatmap({ horizon }),
    enabled: tab === 'heatmap',
  })

  const meta = rankingsQuery.data?.meta
  const overall = rankingsQuery.data?.overall ?? {}
  const shortlist = rankingsQuery.data?.rows ?? []
  const allRows = rankingsQuery.data?.all_rows ?? []
  const running = Boolean(jobId) && !jobDone
  const lastLog = jobQuery.data?.log?.[jobQuery.data.log.length - 1]?.msg ?? ''
  const coverage = metaQuery.data?.coverage ?? {}
  const source = metaQuery.data?.source || meta?.source || ''

  const displayRows = (tab === 'shortlist' ? shortlist : allRows).filter(r =>
    !keyword || formatLabel(r, nameOf).toLowerCase().includes(keyword.toLowerCase()) || r.key.includes(keyword)
  )

  const openDrill = (r: StrategyComboRow) => setDrill({ key: r.key, label: formatLabel(r, nameOf) })

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="组合探索"
        subtitle={meta?.total_signals
          ? `信号池 ${meta.total_signals} 条 · 区间 ${meta.start ?? '—'} ~ ${meta.end ?? '—'} · 来源 ${source === 'strategy_combo' ? '本页扫描' : '策略复盘结果'} · 参与策略 ${meta.strategies?.length ?? 0} 个 · 枚举 ${meta.combo_count} 个组合 · IS/OOS 切分日 ${meta.split_date ?? '—'}。组合评估纯集合运算、不重跑策略；1 日档为「信号日收盘买、次日收盘卖」，含未来数据仅复盘。`
          : '给定策略池自动枚举「两两/三三交集 + 共振规则」，按扣成本后胜率/期望排名，做 IS/OOS 样本外与分段稳定性检验，找高胜率组合。首次打开直接复用策略复盘的信号池；如需更换策略池/区间，点「重建信号池」。'}
      />

      {/* 扫描控制栏 */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-2">
        <button type="button" className="h-8 rounded-btn bg-elevated px-2.5 text-xs text-secondary hover:text-foreground" onClick={() => setShowStrategies(v => !v)}>
          策略池 · 已选 {strategyIds.length} 个 {showStrategies ? '▲' : '▼'}
        </button>
        <DatePicker value={start} onChange={setStart} max={end || undefined} placeholder="开始日期" align="left" />
        <DatePicker value={end} onChange={setEnd} min={start || undefined} placeholder="结束日期" align="left" />
        {(start || end) && <button type="button" className="h-8 rounded-btn px-2 text-xs text-muted hover:bg-elevated" onClick={() => { setStart(''); setEnd('') }}>清空</button>}
        <label className="flex items-center gap-1 text-xs text-muted">成本%<input type="number" step="0.01" min="0" max="1" value={costPct} onChange={e => setCostPct(e.target.value)} className="h-8 w-14 rounded-btn bg-elevated px-2 text-xs text-foreground outline-none" /></label>
        <label className="flex items-center gap-1 text-xs text-muted"><input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)} />强制重算</label>
        <button type="button" className="inline-flex h-8 items-center gap-1 rounded-btn bg-accent px-3 text-xs font-medium text-white disabled:opacity-40" disabled={strategyIds.length === 0 || running || scan.isPending} onClick={() => scan.mutate()}>
          {running || scan.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}{running ? '正在扫描…' : '重建信号池'}
        </button>
        {running && <button type="button" className="inline-flex h-8 items-center gap-1 rounded-btn bg-elevated px-2 text-xs text-secondary" onClick={() => cancel.mutate()}><Ban className="h-3.5 w-3.5" />取消</button>}
        <span className="text-[11px] text-muted">建议每次 4-8 个策略、区间≤40 交易日；慢策略会自动预检排除。</span>
      </div>

      {showStrategies && (
        <div className="border-b border-border bg-surface/60 px-5 py-2">
          <div className="mb-2 flex items-center gap-2 text-xs text-muted">
            <span>只列可执行策略（共 {presets.length} 个）。已排除的策略仍会标注覆盖状态。</span>
            <button type="button" className="h-7 rounded-btn bg-elevated px-2 hover:text-foreground" onClick={() => setStrategyIds(presets.slice(0, 8).map(p => p.id))}>选前 8 个</button>
            <button type="button" className="h-7 rounded-btn bg-elevated px-2 hover:text-foreground" onClick={() => setStrategyIds([])}>清空</button>
          </div>
          <div className="grid max-h-44 grid-cols-1 gap-1 overflow-auto sm:grid-cols-2 xl:grid-cols-3">
            {presets.map(p => (
              <label key={p.id} className="flex cursor-pointer items-center gap-1.5 rounded px-1 py-0.5 text-xs text-secondary hover:bg-elevated">
                <input type="checkbox" checked={strategyIds.includes(p.id)} onChange={e => setStrategyIds(cur => e.target.checked ? [...cur, p.id] : cur.filter(id => id !== p.id))} />
                <span className="truncate" title={p.description || p.id}>{p.name}</span>
                <span className="shrink-0 font-mono text-[10px] text-muted">{p.id}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      {running && jobQuery.data && (
        <div className="border-b border-border px-5 py-2">
          <div className="flex items-center justify-between text-xs text-secondary"><span>{jobQuery.data.status === 'failed' ? '扫描失败' : lastLog || '正在扫描…'}</span><span>{jobQuery.data.progress}%</span></div>
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-elevated"><div className="h-full bg-accent transition-all" style={{ width: `${jobQuery.data.progress}%` }} /></div>
          {jobQuery.data.status === 'failed' && <div className="mt-1 text-xs text-danger">{jobQuery.data.error}</div>}
        </div>
      )}
      {scan.isError && <div className="border-b border-border px-5 py-2 text-xs text-danger">提交失败：{String(scan.error)}</div>}

      {/* 三档全量统计 */}
      {overall && Object.keys(overall).length > 0 && (
        <div className="grid grid-cols-3 gap-2 border-b border-border px-5 py-2">
          {HORIZONS.map(h => {
            const s = overall[String(h)]
            const active = horizon === h
            return (
              <button key={h} type="button" onClick={() => setHorizon(h)} className={`rounded-lg border px-3 py-1.5 text-left ${active ? 'border-accent/60 bg-elevated' : 'border-border bg-surface hover:border-accent/30'}`}>
                <div className="flex items-baseline justify-between"><span className="text-xs text-secondary">全量 · 持有 {h} 日{active ? ' · 当前' : ''}</span><span className={`text-base font-semibold ${(s?.win_rate ?? 0) >= 0.5 ? 'text-bull' : 'text-bear'}`}>{fmtWr(s?.win_rate)}</span></div>
                <div className="mt-0.5 text-[11px] text-muted">平均 <b className={(s?.avg_ret ?? 0) >= 0 ? 'text-bull' : 'text-bear'}>{fmtPct(s?.avg_ret)}</b> · 盈亏比 {s?.profit_loss_ratio?.toFixed(2) ?? '—'} · 已决 {s?.total ?? 0}</div>
              </button>
            )
          })}
        </div>
      )}

      {/* 评估参数 + Tab */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-2">
        <label className="flex items-center gap-1 text-xs text-muted">排名目标
          <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={rankBy} onChange={e => setRankBy(e.target.value as typeof rankBy)}>
            <option value="composite">综合分(推荐)</option><option value="win_rate">胜率优先</option><option value="avg_ret">平均收益优先</option>
          </select>
        </label>
        <label className="flex items-center gap-1 text-xs text-muted">最小样本<input type="number" min="1" max="2000" value={minSamples} onChange={e => setMinSamples(e.target.value)} className="h-8 w-16 rounded-btn bg-elevated px-2 text-xs text-foreground outline-none" /></label>
        <label className="flex items-center gap-1 text-xs text-muted">TopN<input type="number" min="1" max="50" value={topN} onChange={e => setTopN(e.target.value)} className="h-8 w-14 rounded-btn bg-elevated px-2 text-xs text-foreground outline-none" /></label>
        <label className="flex items-center gap-1 text-xs text-muted">IS比例
          <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={isRatio} onChange={e => setIsRatio(e.target.value)}>
            <option value="0.6">60%</option><option value="0.7">70%</option><option value="0.8">80%</option>
          </select>
        </label>
        <label className="flex items-center gap-1 text-xs text-muted">组合规模
          <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={maxComboSize} onChange={e => setMaxComboSize(e.target.value)}>
            <option value="2">≤2</option><option value="3">≤3</option>
          </select>
        </label>
        <div className="ml-auto flex items-center gap-1">
          {(['shortlist', 'all', 'heatmap', 'coverage'] as Tab[]).map(t => (
            <button key={t} type="button" onClick={() => setTab(t)} className={`h-8 rounded-btn px-2.5 text-xs ${tab === t ? 'bg-accent text-white' : 'bg-elevated text-secondary hover:text-foreground'}`}>
              {t === 'shortlist' ? '终榜' : t === 'all' ? '全部组合' : t === 'heatmap' ? '胜率热力图' : '覆盖状态'}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {rankingsQuery.isLoading ? <div className="py-16 text-center text-sm text-muted">正在计算组合排名…</div>
          : rankingsQuery.isError ? <ErrorState hint="组合排名没有读出来。" onRetry={() => rankingsQuery.refetch()} retrying={rankingsQuery.isRefetching} />
          : rankingsQuery.data?.computed === false ? <EmptyState icon={Search} title="还没有信号池" hint="选择策略池与区间，点「重建信号池」；或先到策略复盘页运行一次选股。" />
          : tab === 'heatmap' ? <HeatmapView data={heatmapQuery.data} nameOf={nameOf} loading={heatmapQuery.isLoading} onPick={(a, b) => {
              const ids = a === b ? [a] : [a, b].sort()
              setDrill({ key: 'combo:' + ids.join('+'), label: ids.map(s => nameOf[s] || s).join(' ∩ ') })
            }} />
          : tab === 'coverage' ? <CoverageView coverage={coverage} nameOf={nameOf} source={source} metaInfo={metaQuery.data?.meta} />
          : <RankingTable rows={displayRows} shortlist={tab === 'shortlist'} rankBy={rankBy} nameOf={nameOf} keyword={keyword} setKeyword={setKeyword} onDrill={openDrill} />
        }
      </div>

      {drill && <ComboSignalsDialog comboKey={drill.key} label={drill.label} horizon={horizon} onClose={() => setDrill(null)} />}
    </div>
  )
}

function RankingTable({ rows, shortlist, rankBy, nameOf, keyword, setKeyword, onDrill }: {
  rows: StrategyComboRow[]
  shortlist: boolean
  rankBy: string
  nameOf: Record<string, string>
  keyword: string
  setKeyword: (v: string) => void
  onDrill: (r: StrategyComboRow) => void
}) {
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-2">
        <span className="text-xs text-muted">{shortlist ? `终榜 ${rows.length} 个组合：IS 选 Top-N → 按 OOS 综合分终排，标注过拟合退化。` : `全部 ${rows.length} 个组合，按全区间综合分排序。`}</span>
        <label className="relative ml-auto"><Search className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted" /><input value={keyword} onChange={e => setKeyword(e.target.value)} placeholder="筛选组合" className="h-8 w-44 rounded-btn bg-elevated pl-7 pr-2 text-xs text-foreground outline-none" /></label>
      </div>
      {rows.length === 0 ? <EmptyState title="没有符合条件的组合" hint="放宽最小样本或改排名目标，或重建信号池。" /> : (
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-surface text-secondary">
            <tr className="border-b border-border text-left">
              <th className="px-3 py-2 font-medium">#</th>
              <th className="px-3 py-2 font-medium">组合</th>
              <th className="px-3 py-2 font-medium">全区间(样本/胜率/收益/盈亏比)</th>
              <th className="px-3 py-2 font-medium">IS(样本内)</th>
              <th className="px-3 py-2 font-medium">OOS(样本外)</th>
              <th className="px-3 py-2 font-medium">gap / 退化</th>
              <th className="px-3 py-2 font-medium">四段稳定性</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.key} className="border-b border-border/60 hover:bg-elevated/50">
                <td className="px-3 py-2 text-muted">{shortlist ? (r.final_rank ?? i + 1) : i + 1}</td>
                <td className="max-w-[220px] px-3 py-2">
                  <button type="button" className="text-left text-foreground hover:text-accent" onClick={() => onDrill(r)} title="点击查看信号明细">
                    <div className="truncate">{formatLabel(r, nameOf)}</div>
                  </button>
                  <div className="mt-0.5 flex items-center gap-1 text-[10px] text-muted">
                    <span className={r.kind === 'resonance' ? 'rounded bg-accent/15 px-1 text-accent' : 'rounded bg-elevated px-1'}>{r.kind === 'resonance' ? '共振规则' : `交集 ${r.size}`}</span>
                    {r.insufficient && <span className="text-muted">样本不足</span>}
                  </div>
                </td>
                <td className="px-3 py-2"><StatBar stat={r.all} rankBy={rankBy} /><div className="text-[10px] text-muted">n={r.all?.n ?? 0}</div></td>
                <td className="px-3 py-2"><StatBar stat={r.is} rankBy={rankBy} /><div className="text-[10px] text-muted">n={r.is?.n ?? 0}</div></td>
                <td className="px-3 py-2"><StatBar stat={r.oos} rankBy={rankBy} /><div className="text-[10px] text-muted">n={r.oos?.n ?? 0}</div></td>
                <td className="px-3 py-2">
                  <span className={r.overfit_gap != null && r.overfit_gap > 0.15 ? 'text-bear' : 'text-muted'}>{r.overfit_gap != null ? `${r.overfit_gap > 0 ? '+' : ''}${r.overfit_gap.toFixed(3)}` : '—'}</span>
                  {r.degraded && <span className="ml-1 rounded bg-bear/15 px-1 text-bear">退化</span>}
                </td>
                <td className="px-3 py-2">{r.all ? <SegBars segWrs={r.all.seg_wrs} /> : <span className="text-muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function HeatmapView({ data, nameOf, loading, onPick }: {
  data: { strategies: string[]; cells: ({ win_rate: number; n: number; avg_ret: number } | null)[][] } | undefined
  nameOf: Record<string, string>
  loading: boolean
  onPick: (a: string, b: string) => void
}) {
  if (loading) return <div className="py-16 text-center text-sm text-muted">正在计算胜率矩阵…</div>
  if (!data || data.strategies.length === 0) return <EmptyState title="没有可画的热力图" hint="信号池里没有足够的策略命中。" />
  const short = (sid: string) => (nameOf[sid] || sid).slice(0, 6)
  return (
    <div className="overflow-auto p-5">
      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-muted">
        <span>两两策略交集 1 日档胜率（对角 = 单策略）。点击格子下钻。</span>
        <span className="flex items-center gap-1"><span className="inline-block h-3 w-3 rounded" style={{ background: heatColor(0.35, 1) }} />35%↓</span>
        <span className="flex items-center gap-1"><span className="inline-block h-3 w-3 rounded" style={{ background: heatColor(0.48, 1) }} />48%</span>
        <span className="flex items-center gap-1"><span className="inline-block h-3 w-3 rounded" style={{ background: heatColor(0.6, 1) }} />60%↑</span>
        <span className="text-[11px]">红=高胜率、绿=低胜率（A 股语境）</span>
      </div>
      <table className="border-collapse text-[10px]">
        <thead>
          <tr><th className="sticky left-0 bg-surface p-1" />{data.strategies.map(s => <th key={s} className="p-1 font-medium text-secondary" title={nameOf[s] || s}><div className="w-14 truncate">{short(s)}</div></th>)}</tr>
        </thead>
        <tbody>
          {data.strategies.map((a, i) => (
            <tr key={a}>
              <th className="sticky left-0 bg-surface p-1 text-right font-medium text-secondary" title={nameOf[a] || a}><div className="w-20 truncate text-right">{short(a)}</div></th>
              {data.strategies.map((b, j) => {
                const c = data.cells[i]?.[j]
                return (
                  <td key={b} className="p-0.5">
                    <button type="button" onClick={() => onPick(a, b)} disabled={!c}
                      className="grid h-9 w-14 place-items-center rounded text-white disabled:cursor-default"
                      style={{ background: c ? heatColor(c.win_rate, c.n) : 'transparent', opacity: c ? 1 : 0.35 }}
                      title={`${nameOf[a] || a} ∩ ${nameOf[b] || b}: 胜率 ${fmtWr(c?.win_rate)} · n=${c?.n ?? 0} · 均 ${fmtPct(c?.avg_ret)}`}>
                      {c ? `${(c.win_rate * 100).toFixed(0)}%` : '—'}
                    </button>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 text-[11px] text-muted">灰色/禁用格 = 该交集无已决样本。</div>
    </div>
  )
}

function CoverageView({ coverage, nameOf, source, metaInfo }: {
  coverage: Record<string, { status: string; rows?: number; reason?: string; elapsed?: number }>
  nameOf: Record<string, string>
  source: string
  metaInfo?: Record<string, unknown>
}) {
  const entries = Object.entries(coverage)
  const statusText: Record<string, string> = {
    ok: '有信号', no_signal: '零命中', too_slow: '预检超时(已跳过)', error: '运行报错', cached: '缓存复用',
  }
  const statusColor: Record<string, string> = {
    ok: 'text-bull', no_signal: 'text-muted', too_slow: 'text-bear', error: 'text-bear', cached: 'text-secondary',
  }
  return (
    <div className="p-5">
      <div className="mb-3 text-xs text-muted">
        信号池来源：<b className="text-foreground">{source === 'strategy_combo' ? '本页扫描产物' : '策略复盘结果（回退）'}</b>
        {metaInfo?.computed_at ? ` · 最近扫描 ${String(metaInfo.computed_at)}` : ''}。
        回退模式下覆盖状态按信号池实际命中统计；点「重建信号池」可得逐策略预检/报错/慢策略的完整状态。
      </div>
      {entries.length === 0 ? <EmptyState title="暂无覆盖状态" hint="重建信号池后展示每个策略的命中情况。" /> : (
        <table className="w-full max-w-2xl text-xs">
          <thead className="text-secondary"><tr className="border-b border-border text-left"><th className="px-3 py-2 font-medium">策略</th><th className="px-3 py-2 font-medium">状态</th><th className="px-3 py-2 font-medium">命中信号</th><th className="px-3 py-2 font-medium">说明</th></tr></thead>
          <tbody>
            {entries.map(([sid, cov]) => (
              <tr key={sid} className="border-b border-border/60">
                <td className="px-3 py-2"><div className="text-foreground">{nameOf[sid] || sid}</div><div className="font-mono text-[10px] text-muted">{sid}</div></td>
                <td className={`px-3 py-2 ${statusColor[cov.status] || 'text-muted'}`}>{statusText[cov.status] || cov.status}</td>
                <td className="px-3 py-2 text-foreground">{cov.rows ?? '—'}</td>
                <td className="px-3 py-2 text-muted">{cov.reason || (cov.elapsed != null ? `预检 ${cov.elapsed}s` : '')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
