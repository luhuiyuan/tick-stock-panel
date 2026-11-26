import { useState, type FormEvent } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowDownRight, ArrowUpRight, ChevronRight, CircleHelp } from 'lucide-react'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import { SettingsModal } from '@/components/data/SettingsModal'
import { api, ApiError } from '@/lib/api'
import type { ChartMarker } from '@/lib/chart-types'
import type { TurningCase, TurningDirection, TurningResult, TurningScope, TurningSignal, TurningSummary } from '@/lib/turningPointReviewTypes'
import { MiniChart } from './shared/MiniChart'

const tabs = { overview: '表现概览', matched: '命中案例', unmatched: '未匹配提醒', missed: '漏报拐点' } as const
type DetailTab = keyof typeof tabs
const pageSize = 24
const btn = 'inline-flex h-8 items-center justify-center gap-1.5 rounded-btn border border-border px-3 text-xs hover:bg-elevated disabled:opacity-40'
const field = 'h-8 w-full rounded-btn border border-border bg-surface px-2 text-xs'
const modalBody = 'min-h-0 flex-1 overflow-y-auto p-4 sm:p-5'
const key = ['turning-point-review'] as const
const pct = (value: number) => `${(value * 100).toFixed(1)}%`
const ratio = (value: number | null, n: number, d: number) => value === null ? '—' : `${pct(value)}（${n}/${d}）`
const kind = (signal: TurningSignal) => signal.type === 'composite' ? '复合信号' : '原子信号'

function message(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 429) return '评估繁忙，请稍后重试。'
    if (error.status === 409) return '行情或信号定义已变化，请重新评估。'
    if (error.status === 401 || error.status === 403) return '没有访问权限，请登录后重试。'
  }
  return error instanceof Error ? error.message : '读取评估失败，请重试。'
}

function caseMarkers(row: TurningCase, confirmation = false): ChartMarker[] {
  const marks: ChartMarker[] = []
  if (row.alert) marks.push({ date: row.alert.start_date, label: '信号首日', color: '#eab308', kind: 'neutral' })
  if (row.pivot) {
    marks.push({ date: row.pivot.date, label: row.direction === 'high' ? 'L2 高点' : 'L2 低点', color: '#38bdf8', kind: 'neutral', above: row.direction === 'high' })
    if (confirmation) marks.push({ date: row.pivot.confirmed_date, label: '局部确认', color: '#a78bfa', kind: 'neutral', above: true })
  }
  const dates = new Set(row.bars.map(b => b.date))
  return marks.filter(mark => dates.has(mark.date))
}

const chartBars = (row: TurningCase) => row.bars.map(bar => ({ ...bar, volume: bar.volume ?? undefined }))

function CaseDetail({ row, back }: { row: TurningCase; back: () => void }) {
  const [tab, setTab] = useState<'chart' | 'event'>('chart')
  return <SettingsModal title={`${row.symbol} · 案例详情`} onClose={back} width="max-w-5xl" contentClassName={modalBody}>
    <button type="button" className="mb-3 text-xs text-accent" onClick={back}>← 返回案例列表</button>
    <div role="tablist" aria-label="案例详情" className="mb-3 flex gap-2">
      <button type="button" role="tab" aria-selected={tab === 'chart'} className={btn} onClick={() => setTab('chart')}>K 线图</button>
      <button type="button" role="tab" aria-selected={tab === 'event'} className={btn} onClick={() => setTab('event')}>事件信息</button>
    </div>
    {row.bars_truncated && <p className="mb-2 text-xs text-amber-200">K 线上下文不足，仅绘制现有日线。</p>}
    {tab === 'chart' ? <>
      <p className="mb-2 text-xs text-muted">黄：信号首日　蓝：L2 拐点　紫：局部确认</p>
      {row.bars.length ? <EChartsCandlestick data={chartBars(row)} markers={caseMarkers(row, true)} visibleBars={row.bars.length} height={380} showMA={false} showInfoBar={false}/> : <p className="text-xs text-muted">该案例无可绘制日线。</p>}
    </> : <dl className="grid gap-2 text-sm text-secondary">
      <div>信号首日：{row.alert?.start_date ?? '—'}</div>
      <div>原始触发：{row.alert?.trigger_dates.join('、') ?? '—'}</div>
      <div>拐点发生：{row.pivot?.date ?? '—'}</div>
      <div>局部确认：{row.pivot?.confirmed_date ?? '—'}（不是永久稳定日）</div>
      <div>提前量：{row.lead_bars == null ? '—' : `${row.lead_bars} 根日 K`}</div>
      <div>后续五根日 K 不利变动：{row.adverse_excursion_5_bars == null ? '—' : pct(row.adverse_excursion_5_bars)}（非交易盈亏）</div>
    </dl>}
  </SettingsModal>
}

function Overview({ summary, result }: { summary: TurningSummary; result: TurningResult }) {
  return <div className="space-y-3 text-sm text-secondary">
    <div>拐点覆盖：{ratio(summary.pivot_coverage, summary.covered_count, summary.covered_count + summary.missed_count)}</div>
    <div>提醒匹配：{ratio(summary.alert_match_rate, summary.matched_alert_count, summary.matched_alert_count + summary.duplicate_alert_count + summary.unmatched_alert_count)}</div>
    <div>提前量：1–5 根 {summary.lead_windows['1_5']} · 6–15 根 {summary.lead_windows['6_15']} · 16–30 根 {summary.lead_windows['16_30']}</div>
    <div>未匹配 {summary.unmatched_alert_count} · 重复 {summary.duplicate_alert_count} · 漏报 {summary.missed_count}</div>
    <div>不可判定：{summary.indeterminate_pivot_count} 个拐点 · {summary.indeterminate_alert_count} 条提醒</div>
    <ul className="space-y-1 border-t border-border pt-3 text-xs">
      {result.results.map(({ data, signals }) => {
        const signal = signals.find(s => s.signal_id === summary.signal_id)
        return <li key={data.symbol}>
          {data.symbol} · {signal?.reason ?? (signal?.status === 'partial' ? `${signal.unknown_bars} 根日 K 不可计算` : '可计算')}
          {Object.entries(signal?.limit_price_sources ?? {}).map(([field, source]) => <div key={field} className="text-muted">
            {field === 'limit_up_price' ? '涨停价' : '跌停价'}：同日记录 {source.recorded} · 规则推断 {source.inferred} · 未知 {source.unknown} 根日 K
          </div>)}
          {signal?.limit_price_sources && <p className="text-muted">规则推断可能参考后续行情，不是官方记录。</p>}
        </li>
      })}
    </ul>
  </div>
}

function SignalDetail({ signal, summary, result, scope, close }: {
  signal: TurningSignal; summary: TurningSummary; result: TurningResult; scope: TurningScope; close: () => void
}) {
  const [tab, setTab] = useState<DetailTab>('overview')
  const [offset, setOffset] = useState(0)
  const [selected, select] = useState<TurningCase | null>(null)
  const { symbols, start, end } = scope
  const snapshot = result.snapshot_sha256
  const direction = summary.direction
  const id = signal.signal_id
  const page = useQuery({
    queryKey: [...key, 'cases', symbols, start, end, snapshot, id, direction, tab, offset],
    queryFn: () => tab === 'overview' ? Promise.reject(new Error('概览不请求案例')) : api.turningPointCases({ symbols, start, end }, id, direction, tab, offset, snapshot),
    enabled: tab !== 'overview' && summary.evaluated_symbols > 0,
    staleTime: Infinity, refetchOnWindowFocus: false, retry: false,
  })
  if (selected) return <CaseDetail key={selected.case_id} row={selected} back={() => select(null)}/>
  const stale = page.error instanceof ApiError && page.error.status === 409
  return <SettingsModal title={`${signal.name} · L2 ${direction === 'high' ? '高点' : '低点'}`} onClose={close} width="max-w-5xl" contentClassName={modalBody}>
    <div role="tablist" aria-label="信号详情分类" className="mb-4 flex gap-1 overflow-x-auto border-b border-border pb-2">
      {(Object.keys(tabs) as DetailTab[]).map(t => <button type="button" role="tab" aria-selected={tab === t} key={t} onClick={() => { setTab(t); setOffset(0) }} className={`${btn} shrink-0 aria-selected:border-accent`}>{tabs[t]}</button>)}
    </div>
    {tab === 'overview' ? <Overview summary={summary} result={result}/> : summary.evaluated_symbols === 0 ? <p className="text-xs text-muted">信号不可计算，原因见表现概览。</p> : page.isError ? <div role="alert" className="space-y-2 text-sm text-amber-200"><p>{message(page.error)}</p>{!stale && <button type="button" className={btn} onClick={() => void page.refetch()}>重试</button>}</div> : page.isPending ? <p role="status" className="text-xs text-muted">正在读取本页 K 线…</p> : <>
      {page.data.errors.length > 0 && <p className="mb-2 text-xs text-amber-200">部分标的不可用，详见研究口径。</p>}
      <p className="mb-2 text-xs text-muted">黄线：信号首日　蓝线：L2 拐点　·　点击缩略图放大</p>
      {page.data.cases.length === 0 ? <p className="text-sm text-muted">该页没有案例。</p> : <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {page.data.cases.map(row => <button type="button" key={row.case_id} onClick={() => select(row)} aria-label={`${row.symbol} ${row.pivot?.date ?? row.alert?.start_date} 放大 K 线`} className="min-w-0 rounded border border-border bg-surface p-2 text-left hover:border-accent">
          <div className="text-sm font-medium">{row.symbol}</div>
          <div className="text-xs text-muted">{row.pivot?.date ?? row.alert?.start_date}{row.lead_bars == null ? '' : ` · 提前 ${row.lead_bars} 根`}</div>
          <MiniChart bars={chartBars(row)} spec={{ vlines: caseMarkers(row).map(m => ({ date: m.date, color: m.color! })) }}/>
          {row.bars_truncated && <span className="text-xs text-amber-200">K 线上下文不足</span>}
        </button>)}
      </div>}
      {page.data.total > pageSize && <nav aria-label="案例分页" className="mt-4 flex flex-wrap items-center justify-center gap-2 text-xs text-muted">
        <button className={btn} disabled={offset === 0} onClick={() => setOffset(offset - pageSize)}>上一页</button>
        <span>{offset / pageSize + 1} / {Math.ceil(page.data.total / pageSize)} 页 · {page.data.total} 条</span>
        <button className={btn} disabled={offset + pageSize >= page.data.total} onClick={() => setOffset(offset + pageSize)}>下一页</button>
      </nav>}
    </>}
  </SettingsModal>
}

export function TurningPointSignalReview() {
  const [direction, setDirection] = useState<TurningDirection>('low')
  const [selected, select] = useState<string | null>(null)
  const [scopeOpen, setScopeOpen] = useState(false)
  const [picker, setPicker] = useState(false)
  const [search, setSearch] = useState('')
  const [ids, setIds] = useState<string[]>([])
  const [form, setForm] = useState({ symbols: '', start: '', end: '' })
  const [validation, setValidation] = useState('')
  const catalog = useQuery({ queryKey: [...key, 'signals'], queryFn: api.turningPointSignals, retry: false })
  const rules = useQuery({ queryKey: [...key, 'scope'], queryFn: api.turningPointScope, enabled: scopeOpen, retry: false })
  const run = useMutation({ mutationFn: api.turningPointReview })
  const result = run.data
  const applied = run.variables
  const all = catalog.data?.signals ?? []
  const byId = new Map([...all, ...(result?.results.flatMap(r => r.signals) ?? [])].map(s => [s.signal_id, s]))
  const rows = result?.summary.filter(r => r.direction === direction) ?? []
  const active = rows.find(row => row.signal_id === selected)

  function submit(event: FormEvent) {
    event.preventDefault()
    const symbols = [...new Set(form.symbols.toUpperCase().split(/[,，\s]+/).filter(Boolean))]
    if (!symbols.length || symbols.length > 5 || symbols.some(s => !/^\d{6}\.(SH|SZ|BJ)$/.test(s))) {
      setValidation('请选择 1–5 个带后缀的股票代码，如 600519.SH。'); return
    }
    const duration = (Date.parse(form.end) - Date.parse(form.start)) / 86400000
    if (!(duration >= 0 && duration < 366)) { setValidation('请输入有效的起止日期，最多 366 个自然日。'); return }
    if (!ids.length) { setValidation('请先选择至少一个日线信号。'); return }
    setValidation(''); select(null)
    run.mutate({ ...form, symbols: symbols.join(','), signal_ids: ids.join(',') })
  }

  return <section className="min-h-0 flex-1 space-y-5 overflow-auto p-4 sm:p-6">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><h1 className="text-lg font-semibold tracking-tight">拐点信号评估</h1><p className="mt-1 text-xs text-muted">从历史 L2 拐点反查候选信号，再核对未匹配、漏报和提前量。</p></div>
      <button type="button" className={btn} onClick={() => setScopeOpen(true)}>研究口径</button>
    </header>
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2 rounded-lg border border-border bg-surface p-3">
      <label className="min-w-48 flex-1 text-xs text-muted">股票代码<input className={field} placeholder="600519.SH, 000858.SZ" value={form.symbols} onChange={e => setForm({ ...form, symbols: e.target.value })}/></label>
      <label className="text-xs text-muted">开始日期<input type="date" className={field} value={form.start} onChange={e => setForm({ ...form, start: e.target.value })}/></label>
      <label className="text-xs text-muted">结束日期<input type="date" className={field} value={form.end} onChange={e => setForm({ ...form, end: e.target.value })}/></label>
      <button type="button" className={btn} onClick={() => { setPicker(true); setSearch('') }}>选择信号（{ids.length}/10）</button>
      <button type="submit" className={`${btn} bg-accent/10 text-accent`} disabled={run.isPending || !catalog.data}>{run.isPending ? '正在评估…' : '开始评估'}</button>
      {validation && <p role="alert" className="w-full text-xs text-amber-200">{validation}</p>}
    </form>
    {run.isError && <p role="alert" className="text-xs text-amber-200">{message(run.error)}</p>}
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-2">
      <div role="tablist" aria-label="评估的拐点方向" className="flex gap-1">
        {([{ id: 'high', label: 'L2 高点', icon: ArrowUpRight }, { id: 'low', label: 'L2 低点', icon: ArrowDownRight }] as const).map(({ id, label, icon: Icon }) => <button type="button" role="tab" key={id} aria-selected={direction === id} className={`${btn} aria-selected:border-accent`} onClick={() => { setDirection(id); select(null) }}><Icon size={14}/>{label}</button>)}
      </div>
      <span className="inline-flex items-center gap-1 text-xs text-muted"><CircleHelp size={13}/>事后标签可能随未来行情修订</span>
    </div>
    {result && applied && <div className="space-y-1 text-xs text-muted">
      <p>已评估：{applied.start} 至 {applied.end} · {applied.symbols.replaceAll(',', '、')}</p>
      {result.missing_market_partitions.length > 0 && <p className="text-amber-200">缺 {result.missing_market_partitions.length} 个交易日分区，日期见研究口径。</p>}
      {result.errors.length > 0 && <p className="text-amber-200">{result.errors.length} 只标的不可用，原因见研究口径。</p>}
    </div>}
    <div className="overflow-hidden rounded-xl border border-border bg-surface">
      <h2 className="border-b border-border px-4 py-3 text-sm font-medium">候选信号</h2>
      {!rows.length && <p className="p-4 text-xs text-muted">请选择研究范围并开始评估。</p>}
      {rows.map(row => {
        const signal = byId.get(row.signal_id)
        const reasons = [...new Set(result?.results.flatMap(item => item.signals
          .filter(s => s.signal_id === row.signal_id && s.reason).map(s => s.reason)) ?? [])]
        const unavailable = reasons[0] ?? result?.errors[0]?.reason ?? '没有可用样本'
        return <button type="button" key={row.signal_id} className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-4 py-3 text-left text-xs hover:bg-elevated/50" onClick={() => select(row.signal_id)}>
          <span className="min-w-36 flex-1 text-sm font-medium">{signal?.name ?? row.signal_id} {signal && <small className="ml-1 text-muted">{kind(signal)}</small>}</span>
          {row.evaluated_symbols === 0 ? <span className="max-w-full break-words text-amber-200">不可计算：{unavailable}{reasons.length > 1 ? '等，详见详情' : ''}</span> : <><span>覆盖 {ratio(row.pivot_coverage, row.covered_count, row.covered_count + row.missed_count)}</span><span>匹配 {ratio(row.alert_match_rate, row.matched_alert_count, row.matched_alert_count + row.duplicate_alert_count + row.unmatched_alert_count)}</span></>}
          <ChevronRight size={14}/>
        </button>
      })}
    </div>
    {picker && <SettingsModal title="选择日线信号" onClose={() => setPicker(false)} width="max-w-xl" contentClassName={modalBody}>
      <input className={field} aria-label="搜索信号" placeholder="搜索名称或 ID" value={search} onChange={e => setSearch(e.target.value)}/>
      {catalog.isPending && <p role="status">正在读取信号…</p>}
      {catalog.isError && <p role="alert">{message(catalog.error)} <button className={btn} onClick={() => void catalog.refetch()}>重试</button></p>}
      {all.filter(s => s.enabled && s.timeframe === 'daily' && `${s.name} ${s.signal_id}`.toLowerCase().includes(search.trim().toLowerCase())).map(s => <label key={s.signal_id} className="flex items-center gap-2 border-b border-border py-2 text-sm">
        <input type="checkbox" checked={ids.includes(s.signal_id)} disabled={ids.length >= 10 && !ids.includes(s.signal_id)} onChange={e => setIds(e.target.checked ? [...ids, s.signal_id] : ids.filter(id => id !== s.signal_id))}/>{s.name}<span className="text-xs text-muted">{s.signal_id} · {kind(s)}</span>
      </label>)}
      <button type="button" className={`${btn} mt-3`} onClick={() => setPicker(false)}>完成（{ids.length}）</button>
    </SettingsModal>}
    {scopeOpen && <SettingsModal title="研究口径" onClose={() => setScopeOpen(false)} width="max-w-2xl" contentClassName={modalBody}>
      {rules.isPending && <p role="status">正在读取研究口径…</p>}
      {rules.isError && <p role="alert">{message(rules.error)}</p>}
      <ul className="space-y-2 text-xs text-secondary">{rules.data?.notes.map(note => <li key={note}>{note}</li>)}</ul>
      {result && <div className="mt-3 space-y-1 border-t border-border pt-3 text-xs text-muted">
        <p>实际范围：{applied?.start} 至 {applied?.end} · {applied?.symbols}</p>
        <p>缺分区：{result.missing_market_partitions.join('、') || '未发现'}</p>
        {result.errors.map(e => <p key={e.symbol}>{e.symbol}：{e.reason}</p>)}
      </div>}
    </SettingsModal>}
    {active && selected && result && applied && byId.has(selected) && <SignalDetail key={`${direction}:${selected}`} signal={byId.get(selected)!} summary={active} result={result} scope={applied} close={() => select(null)}/>}
  </section>
}
