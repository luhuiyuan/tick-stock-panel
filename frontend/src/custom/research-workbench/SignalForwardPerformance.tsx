import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type SignalForwardEvent, type SignalForwardLatest, type SignalForwardSummary } from '@/lib/api'
import { MiniChart } from './shared/MiniChart'
import { ZoomDialog } from './shared/ZoomDialog'
import type { ResearchCtx, ResearchRecord } from './shared/types'

const horizons = [1, 5, 10] as const
const halves = { discovery: '发现期', validation: '验证期' } as const
const statusNames: Record<string, string> = { executable: '理论可入场', not_executable: '不可入场', unknown: '证据不足' }
const sourceNames: Record<string, string> = { dated_instrument: '同日维表', rule_inferred: '规则推断' }
const reasons: Record<string, string> = {
  open_at_upper_limit: '开盘触及涨停价', below_upper_limit_or_unlimited: '开盘未达涨停或当日无限价',
  entry_outside_split: '入场日超出本阶段', missing_next_session_bar: '次日缺 K 线',
  missing_open_or_limit_evidence: '缺开盘价或限价依据',
}
const btn = 'rounded-btn bg-elevated px-3 py-1.5 text-sm disabled:opacity-45 aria-pressed:text-accent'
const cell = 'p-2 text-right'
const size = 24
const pct = (v: number | null | undefined, signed = true) => v == null ? '—' : `${signed && v > 0 ? '+' : ''}${(v * 100).toFixed(2)}%`
const tone = (v: number | null | undefined) => v == null || v === 0 ? 'text-muted' : v > 0 ? 'text-bull' : 'text-bear'
type Horizon = typeof horizons[number]
type Split = keyof typeof halves
const caseId = (row: SignalForwardEvent) => `${row.symbol}-${row.signal_date}`

export function caseMarks(row: SignalForwardEvent, h: Horizon) {
  const dates = new Set(row.bars.map(bar => bar.date))
  const marks = [
    {date: row.signal_date, color: '#eab308', label: '触发'},
    {date: row.execution_reason === 'entry_outside_split' ? null : row.entry_date, color: '#38bdf8', label: '入场参考'},
    {date: row[`return_${h}d`] == null ? null : row[`exit_date_${h}d`], color: '#a78bfa', label: `${h}日退出参考`},
  ]
  return marks.flatMap(mark => mark.date && dates.has(mark.date) ? [{ ...mark, date: mark.date }] : [])
}

function Results({ data }: { data: SignalForwardLatest }) {
  const [signal, setSignal] = useState(data.config.signal_ids[0] ?? '')
  const [split, setSplit] = useState<Split>('validation')
  const [h, setH] = useState<Horizon>(5)
  const [symbol, setSymbol] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const key = ['signal-forward-events', data.run_id, signal, split, symbol, page] as const
  const events = useQuery({queryKey: key, enabled: Boolean(signal), queryFn: () => api.signalForwardEvents(data.run_id, signal, split, symbol, (page - 1) * size)})
  const rows = events.data?.rows ?? []
  const matching = (r: SignalForwardSummary) => r.signal_id === signal && r.split === split
  const summary = data.summary.filter(matching)
  const monthly = data.monthly.filter(r => matching(r) && r.horizon === h)
  const total = events.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / size))
  const resetCases = () => { setPage(1); setSelected(null) }
  const names = new Map(data.summary.map(r => [r.signal_id, r.signal_name]))
  const zoomRows: ResearchRecord[] = rows.map(r => ({
    id: caseId(r), symbol: r.symbol, name: '', startDate: r.signal_date,
    endDate: r.signal_date, bars: r.bars, raw: {},
  }))
  const ctx: ResearchCtx = {start: '', end: '', q: '', sort: '', order: 'desc', offset: 0, limit: size, extra: {}}

  return <section className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
    <div>
      <h2 className="text-lg font-semibold">信号触发后表现</h2>
      <p className="text-xs text-muted">{data.config.date_from} 至 {data.config.date_to} · {data.config.split_date} 起为验证期 · {data.config.symbols.length} 只股票 · 任务 {data.run_id}</p>
      <p className="text-xs text-muted">信号后首个交易日开盘为入场参考，最早再下一交易日收盘计 1 日收益。不代表真实成交。</p>
      {data.stale && <p role="status" className="text-sm text-muted">当前输入与已保存结果不同。收益保留原结果；已变化的行情分区不画 K 线。</p>}
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <label className="text-sm">信号 <select className="rounded border border-border bg-surface px-2 py-1" value={signal} onChange={e => {setSignal(e.target.value); resetCases()}}>
        {data.config.signal_ids.map(id => <option key={id} value={id}>{names.get(id) ?? id}</option>)}
      </select></label>
      {(Object.keys(halves) as Split[]).map(s => <button key={s} className={btn} aria-pressed={s === split} onClick={() => {setSplit(s); resetCases()}}>{halves[s]}</button>)}
    </div>
    <div className="overflow-x-auto rounded border border-border">
      <table className="w-full whitespace-nowrap text-sm tabular-nums">
        <caption className="p-2 text-left">{halves[split]}汇总{summary.length > 0 && ` · ${summary[0].first_trigger_count} 次触发 / ${summary[0].executable_count} 次理论可入场 / ${summary[0].unknown_execution_count} 次证据不足`}</caption>
        <thead className="bg-elevated text-xs text-muted"><tr>{['持有期','可评估','中位收益','正收益率','平均最大不利走势','平均超额收益'].map(label => <th className={cell} key={label}>{label}</th>)}</tr></thead>
        <tbody>{summary.map(r => <tr className="border-t border-border" key={r.horizon}>
          <th className={cell}>{r.horizon} 日</th><td className={cell}>{r.evaluable_count}</td><td className={`${cell} ${tone(r.median_return)}`}>{pct(r.median_return)}</td>
          <td className={cell}>{pct(r.positive_return_rate, false)}</td><td className={cell}>{pct(r.mean_max_adverse)}</td><td className={cell}>{pct(r.mean_excess_return)}</td>
        </tr>)}</tbody>
      </table>
      {!summary.length && <p className="p-2 text-xs text-muted">此阶段没有触发记录。</p>}
    </div>
    <div>
      <div className="flex flex-wrap items-center gap-2"><h3 className="font-medium">月度表现</h3>
        {horizons.map(n => <button key={n} className={btn} aria-pressed={n === h} onClick={() => setH(n)}>{n} 日</button>)}
      </div>
      <div className="mt-2 flex gap-2 overflow-x-auto">
        {monthly.map(r => <div key={r.month} className="min-w-24 rounded border border-border bg-surface p-2 text-xs tabular-nums">
          <p>{r.month}</p><p className={tone(r.median_return)}>{pct(r.median_return)}</p>
          <p className="text-muted">{r.evaluable_count} 笔{r.evaluable_count > 0 && r.evaluable_count < 3 ? ' · 样本少' : ''}</p>
        </div>)}
        {!monthly.length && <p className="text-xs text-muted">没有月度记录。</p>}
      </div>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-medium">触发案例 · K 线缩略图</h3>
      <label className="text-sm">股票 <select className="rounded border border-border bg-surface px-2 py-1" value={symbol} onChange={e => {setSymbol(e.target.value); resetCases()}}>
        <option value="">全部股票</option>{data.config.symbols.map(code => <option key={code}>{code}</option>)}
      </select></label>
    </div>
    <p className="text-xs text-muted">黄线：触发日 · 蓝线：入场参考日 · 紫线：{h} 日退出参考日 · 缺 K 线不顺延，无可评估收益时不画退出线 · 点击放大</p>
    {events.isError ? <p role="alert">触发案例读取失败。 <button className={btn} onClick={() => void events.refetch()}>重试</button></p>
      : events.isLoading ? <p role="status" className="text-sm text-muted">正在读取本页 K 线…</p>
      : rows.length === 0 ? <p className="text-sm text-muted">该条件下没有触发案例。</p>
      : <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {rows.map(row => <button key={caseId(row)} type="button" onClick={() => setSelected(caseId(row))}
          aria-label={`${row.symbol} ${row.signal_date} 放大 K 线`} className="rounded border border-border bg-surface p-2 text-left hover:border-accent">
          <div className="truncate text-sm font-medium">{row.symbol}</div>
          <div className="text-xs text-muted">触发 {row.signal_date} · {statusNames[row.execution_status] ?? '未知'}</div>
          <MiniChart bars={row.bars} spec={{vlines:caseMarks(row, h)}} />
          <div className="grid grid-cols-3 gap-1 text-xs tabular-nums">{horizons.map(n => <span key={n} className={tone(row[`return_${n}d`])}>{n} 日 {pct(row[`return_${n}d`])}</span>)}</div>
        </button>)}
      </div>}
    <nav aria-label="触发案例分页" className="flex items-center justify-between border-t border-border py-2 text-xs text-muted">
      <span>共 {total} 条 · 每页 {size} 条</span><div className="flex items-center gap-2">
        <button className={btn} disabled={page <= 1} onClick={() => {setSelected(null); setPage(page - 1)}}>上一页</button>{page}/{pages}
        <button className={btn} disabled={page >= pages} onClick={() => {setSelected(null); setPage(page + 1)}}>下一页</button>
      </div>
    </nav>
    <details className="text-xs text-muted"><summary>研究限制与数据覆盖</summary>
      <p>版本 {data.rule}。收益基于复权价格，不含真实撮合、费用或滑点；ST 历史推断可能误判。超额收益基准仅为本次样本股票。</p>
      <p>{data.missing_dates.length ? `研究区间缺行情日期：${data.missing_dates.join('、')}` : '研究区间没有缺失的整日分区。'} `—` 表示无可用数值。</p>
    </details>
    {selected && <ZoomDialog rows={zoomRows} currentId={selected} ctx={ctx} keyPrefix={key} windowLabel="触发日前后的"
      onClose={() => setSelected(null)} onSelect={setSelected} recipe={{
        zoomNouns: {beforeWindow: '触发前', spanNoun: '观察区间', afterWindow: '触发后'},
        fetchZoom: async r => ({bars: r.bars, fromDate: r.bars[0]?.date ?? r.startDate,
          toDate: r.bars.at(-1)?.date ?? r.endDate,
          barsBefore: r.bars.filter(bar => bar.date < r.startDate).length,
          barsAfter: r.bars.filter(bar => bar.date > r.startDate).length}),
        dialogDecor: r => {
          const row = rows.find(e => caseId(e) === r.id)!
          const source = row.entry_limit_source ? sourceNames[row.entry_limit_source] : null
          return {subtitle: `${statusNames[row.execution_status] ?? '未知'} · ${reasons[row.execution_reason] ?? '无判定说明'} · 限价：${source ?? '未知'} · ${h} 日：有利 ${pct(row[`max_favorable_${h}d`])} / 不利 ${pct(row[`max_adverse_${h}d`])}`,
            ranges: [], focus: 'all', markers: caseMarks(row, h).map(mark => ({...mark, kind:'neutral' as const, above: true}))}
        },
      }} />}
  </section>
}

export function SignalForwardPerformance() {
  const latest = useQuery({queryKey:['signal-forward-latest'], queryFn:api.signalForwardLatest})
  if (latest.isLoading) return <p role="status" className="p-4 text-sm text-muted">正在读取触发后表现…</p>
  if (latest.isError) return <p role="alert" className="p-4 text-sm">读取研究结果失败。 <button className={btn} onClick={() => void latest.refetch()}>重试</button></p>
  if (!latest.data) return <p className="p-4 text-sm text-muted">尚无成功的触发后表现研究结果。</p>
  return <Results key={latest.data.run_id} data={latest.data} />
}
