import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type ForwardStartResult, type SignalForwardContrast, type SignalForwardEvent, type SignalForwardJob, type SignalForwardLatest, type SignalForwardSummary } from '@/lib/api'
import { MiniChart } from './shared/MiniChart'
import { ZoomDialog } from './shared/ZoomDialog'
import type { ResearchCtx, ResearchRecord } from './shared/types'

const horizons = [1, 5, 10] as const
const halves = { discovery: '发现期', validation: '验证期' } as const
const CONTRAST_SPLITS = ['all', 'discovery', 'validation', 'early', 'mid', 'recent'] as const
const splitNames: Record<string, string> = { all: '全区间', discovery: '发现期', validation: '验证期', early: '早期', mid: '中期', recent: '最近' }
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
const UP = '#C74040'   // 涨=红
const DOWN = '#2D9B65' // 跌=绿
const GREY = '#8a8f98'

export function caseMarks(row: SignalForwardEvent, h: Horizon) {
  const dates = new Set(row.bars.map(bar => bar.date))
  const marks = [
    {date: row.signal_date, color: '#eab308', label: '触发'},
    {date: row.execution_reason === 'entry_outside_split' ? null : row.entry_date, color: '#38bdf8', label: '入场参考'},
    {date: row[`return_${h}d`] == null ? null : row[`exit_date_${h}d`], color: '#a78bfa', label: `${h}日退出参考`},
  ]
  return marks.flatMap(mark => mark.date && dates.has(mark.date) ? [{ ...mark, date: mark.date }] : [])
}

// 把"命中组均值 - 未命中组均值"翻译成人能直接用的结论。
type Verdict = { badge: string; color: string; bg: string; line: string; icon: string }
function verdict(diff: number | null, hit: number | null, miss: number | null, h: number): Verdict {
  if (diff == null || hit == null || miss == null) {
    return { badge: '样本不足', color: '#8a8f98', bg: 'rgba(138,143,152,0.12)', icon: '?', line: '这个条件下还没有足够的对照数据，暂时得不出结论。' }
  }
  const d = diff * 100 // 百分点
  if (d <= -0.5) {
    return { badge: '值得回避', color: '#2D9B65', bg: 'rgba(45,155,101,0.14)', icon: '✓',
      line: `信号触发后 ${h} 天，这批股票平均 ${pct(hit)}，比同期没触发的股票（${pct(miss)}）低 ${Math.abs(d).toFixed(2)} 个百分点，回避有据。` }
  }
  if (d <= -0.1) {
    return { badge: '略有价值', color: '#3b82c4', bg: 'rgba(59,130,196,0.14)', icon: '+',
      line: `触发后 ${h} 天平均 ${pct(hit)}，比没触发的（${pct(miss)}）低 ${Math.abs(d).toFixed(2)} 个百分点，差距不大，仅供参考。` }
  }
  if (d < 0.1) {
    return { badge: '差别很小', color: '#c98a17', bg: 'rgba(201,138,23,0.14)', icon: '~',
      line: `触发（${pct(hit)}）与没触发（${pct(miss)}）的表现几乎一样，这个信号对回避帮助不大。` }
  }
  return { badge: '不宜回避', color: '#C74040', bg: 'rgba(199,64,64,0.14)', icon: '!',
    line: `信号触发后 ${h} 天平均 ${pct(hit)}，反而比没触发的（${pct(miss)}）高 ${d.toFixed(2)} 个百分点，拿它回避可能适得其反。` }
}

// 三段稳健性：早/中/近的差值是否都为负（都为负=回避一直站得住）。
function segmentNote(diffs: (number | null)[]) {
  const v = diffs.filter((d): d is number => d != null)
  if (v.length === 0) return { text: '暂无分段数据', ok: false }
  if (v.length === 3 && v.every(d => d < 0)) return { text: '早/中/近三段都成立', ok: true }
  if (v.every(d => d >= 0)) return { text: '各段都不成立', ok: false }
  return { text: '部分时段不成立，不够稳', ok: false }
}

function Bar({ label, value, max }: { label: string; value: number | null; max: number }) {
  const w = value == null ? 0 : Math.min(Math.abs(value) / max, 1) * 100
  const color = value == null ? GREY : value > 0 ? UP : DOWN
  return <div className="flex items-center gap-2">
    <span className="w-[4.5rem] shrink-0 text-xs text-muted">{label}</span>
    <div className="relative h-2.5 flex-1 overflow-hidden rounded-full bg-elevated">
      <div className="absolute inset-y-0 left-0 rounded-full transition-all" style={{ width: `${w}%`, background: color }} />
    </div>
    <span className="w-16 shrink-0 text-right text-xs font-medium tabular-nums" style={{ color }}>{pct(value)}</span>
  </div>
}

function ConclusionCards({ data, h, setH }: { data: SignalForwardLatest; h: number; setH: (n: number) => void }) {
  const [split, setSplit] = useState<string>('all')
  const byKey = useMemo(() => {
    const m = new Map<string, SignalForwardContrast>()
    for (const r of data.contrast) m.set(`${r.signal_id}|${r.split}|${r.horizon}`, r)
    return m
  }, [data.contrast])
  const ids = useMemo(() => [...new Set(data.contrast.map(r => r.signal_id))], [data.contrast])
  const names = useMemo(() => new Map(data.summary.map(r => [r.signal_id, r.signal_name])), [data.summary])

  if (!ids.length) return <section className="rounded-lg border border-border bg-surface p-5 text-sm text-muted">
    本次结果还没有对照数据。点上方「重新研究 / 配置」跑一次，就能看到每个信号值不值得回避。
  </section>

  return <section className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 className="text-lg font-semibold">这些信号，值不值得回避？</h2>
        <p className="text-xs text-muted">对比"信号触发的股票"和"同期没触发的股票"之后的表现，差距越明显，信号越有用。</p>
      </div>
      <div className="flex items-center gap-1">
        <span className="mr-1 text-xs text-muted">看多久</span>
        {horizons.map(n => <button key={n} className={btn} aria-pressed={h === n} onClick={() => setH(n)}>{n} 天</button>)}
      </div>
    </div>
    <div className="flex flex-wrap items-center gap-1">
      <span className="mr-1 text-xs text-muted">统计区间</span>
      {(['all', 'discovery', 'validation'] as const).map(s => <button key={s} className={btn} aria-pressed={split === s} onClick={() => setSplit(s)}>{splitNames[s]}</button>)}
      <span className="ml-2 text-xs text-muted">发现期=用来找信号，验证期=拿没见过的数据检验</span>
    </div>
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {ids.map(id => {
        const cur = byKey.get(`${id}|${split}|${h}`)
        const seg = (['early', 'mid', 'recent'] as const).map(s => byKey.get(`${id}|${s}|${h}`)?.diff ?? null)
        const sn = segmentNote(seg)
        if (!cur) return null
        const v = verdict(cur.diff, cur.hit_mean, cur.miss_mean, h)
        const max = Math.max(Math.abs(cur.hit_mean ?? 0), Math.abs(cur.miss_mean ?? 0), 0.001)
        return <article key={id} className="space-y-3 rounded-lg border border-border bg-surface p-4">
          <div className="flex items-start justify-between gap-2">
            <h3 className="font-semibold leading-tight">{names.get(id) ?? id}</h3>
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold" style={{ color: v.color, background: v.bg }}>
              <span className="grid h-4 w-4 place-items-center rounded-full text-[11px] font-bold" style={{ background: v.color, color: '#fff' }}>{v.icon}</span>
              {v.badge}
            </span>
          </div>
          <p className="text-sm leading-relaxed text-foreground/90">{v.line}</p>
          <div className="space-y-1.5 rounded-md bg-elevated/60 p-2.5">
            <Bar label="触发的股票" value={cur.hit_mean} max={max} />
            <Bar label="没触发的股票" value={cur.miss_mean} max={max} />
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <span>命中 {cur.hit_count.toLocaleString()} 次 · 对照 {cur.miss_count.toLocaleString()} 次</span>
            <span style={{ color: sn.ok ? '#2D9B65' : undefined }}>{sn.ok ? '✓ ' : ''}{sn.text}</span>
          </div>
        </article>
      })}
    </div>
    <details className="rounded-md border border-border bg-surface px-3 py-2 text-xs text-muted">
      <summary className="cursor-pointer">这些结论是怎么算出来的？（点开看口径）</summary>
      <p className="mt-2 leading-relaxed">"触发的股票"= 当天信号命中的个股；"没触发的股票"= 同一天该信号没命中的其余个股。"收益"= 从信号后首个交易日开盘买入参考价算起，持有 {h} 天后的涨跌幅。两段收益的差（命中 − 没触发）就是信号的价值：差得越多（越负），说明命中后确实更弱，回避越有道理。</p>
      <p className="mt-1 leading-relaxed">全区间= 发现期+验证期合并；早/中/近是把全区间按时间切成三段，看结论是不是一直成立。"可入场/证据不足"等成交可行性不影响这里的对比口径。全部为历史统计模拟，不代表真实成交，也不含手续费。</p>
    </details>
  </section>
}

// 专业明细表（折叠收起，给想深挖的人看）
function ContrastTable({ data }: { data: SignalForwardLatest }) {
  const [split, setSplit] = useState<string>('all')
  const [h, setH] = useState<number>(10)
  const sigNames = new Map(data.summary.map(r => [r.signal_id, r.signal_name]))
  const rows = useMemo(() => {
    const byKey = new Map<string, SignalForwardContrast>()
    for (const r of data.contrast) if (r.horizon === h) byKey.set(`${r.signal_id}|${r.split}`, r)
    const ids = [...new Set(data.contrast.map(r => r.signal_id))]
    return ids.map(id => {
      const all = byKey.get(`${id}|all`) ?? byKey.get(`${id}|${split}`)
      const diffs = CONTRAST_SPLITS.slice(3).map(s => byKey.get(`${id}|${s}`)?.diff ?? null)
      return { id, name: sigNames.get(id) ?? id, cur: byKey.get(`${id}|${split}`) ?? all, diffs }
    }).filter((r): r is { id: string; name: string; cur: SignalForwardContrast; diffs: (number|null)[] } => Boolean(r.cur))
  }, [data.contrast, data.summary, split, h])
  if (!data.contrast.length) return null
  return <section className="space-y-2">
    <div className="flex flex-wrap items-center gap-2">
      {horizons.map(n => <button key={n} className={btn} aria-pressed={h === n} onClick={() => setH(n)}>{n} 日</button>)}
      <span className="text-muted">|</span>
      {CONTRAST_SPLITS.map(s => <button key={s} className={btn} aria-pressed={split === s} onClick={() => setSplit(s)}>{splitNames[s]}</button>)}
    </div>
    <p className="text-xs text-muted">差值 = 命中组均值 − 未命中组均值，越负代表命中后表现越差、回避越有道理。三段基于早期/中期/最近。</p>
    <div className="overflow-x-auto rounded border border-border">
      <table className="w-full whitespace-nowrap text-sm tabular-nums">
        <thead className="bg-elevated text-xs text-muted"><tr>
          {['信号','命中率','命中组','未命中组',`T+${h} 差值`,'三段'].map((label, i) => <th key={label} className={`p-2 ${i === 0 ? 'text-left' : 'text-right'}`}>{label}</th>)}
        </tr></thead>
        <tbody>{rows.map(r => {
          const sn = segmentNote(r.diffs)
          return <tr key={r.id} className="border-t border-border">
            <th className="p-2 text-left font-medium">{r.name}</th>
            <td className="p-2 text-right">{(r.cur.hit_rate * 100).toFixed(1)}%</td>
            <td className={`p-2 text-right ${tone(r.cur.hit_mean)}`}>{pct(r.cur.hit_mean)}</td>
            <td className={`p-2 text-right ${tone(r.cur.miss_mean)}`}>{pct(r.cur.miss_mean)}</td>
            <td className={`p-2 text-right font-medium ${tone(r.cur.diff)}`}>{r.cur.diff == null ? '—' : (r.cur.diff * 100).toFixed(2)}</td>
            <td className="p-2 text-right text-xs" style={{ color: sn.ok ? '#2D9B65' : undefined }}>{sn.text}</td>
          </tr>
        })}</tbody>
      </table>
      {!rows.length && <p className="p-2 text-xs text-muted">此窗口没有对照数据。</p>}
    </div>
  </section>
}

function Results({ data }: { data: SignalForwardLatest }) {
  const [h, setH] = useState<number>(10)
  const [signal, setSignal] = useState(data.config.signal_ids[0] ?? '')
  const [split, setSplit] = useState<Split>('validation')
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
  const marketNote = data.config.symbols.length > 1000 ? `全市场 ${data.config.symbols.length.toLocaleString()} 只` : `${data.config.symbols.length} 只`

  return <section className="space-y-5">
    <div>
      <h1 className="text-xl font-semibold">触发后表现</h1>
      <p className="text-xs text-muted">{data.config.date_from} 至 {data.config.date_to} · {marketNote} · {data.config.split_date} 起算验证期 —— 用历史数据检验信号到底有没有用。</p>
    </div>
    <ConclusionCards data={data} h={h} setH={setH} />

    <details className="rounded-lg border border-border bg-surface">
      <summary className="cursor-pointer p-3 text-sm font-medium">查看逐笔明细与月度/阶段数据</summary>
      <div className="space-y-4 border-t border-border p-3">
        <div>
          <h3 className="mb-2 text-sm font-medium">命中 / 未命中 对照表</h3>
          <ContrastTable data={data} />
        </div>
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-sm">信号 <select className="rounded border border-border bg-surface px-2 py-1" value={signal} onChange={e => {setSignal(e.target.value); resetCases()}}>
              {data.config.signal_ids.map(id => <option key={id} value={id}>{names.get(id) ?? id}</option>)}
            </select></label>
            {(Object.keys(halves) as Split[]).map(s => <button key={s} className={btn} aria-pressed={s === split} onClick={() => {setSplit(s); resetCases()}}>{halves[s]}</button>)}
          </div>
          <div className="mt-2 overflow-x-auto rounded border border-border">
            <table className="w-full whitespace-nowrap text-sm tabular-nums">
              <caption className="p-2 text-left">{halves[split]}汇总{summary.length > 0 && ` · ${summary[0].first_trigger_count} 次触发 / ${summary[0].executable_count} 次可评估 / ${summary[0].unknown_execution_count} 次证据不足`}</caption>
              <thead className="bg-elevated text-xs text-muted"><tr>{['持有期','可评估','中位收益','正收益占比','平均最大回撤','平均超额'].map(label => <th className={cell} key={label}>{label}</th>)}</tr></thead>
              <tbody>{summary.map(r => <tr className="border-t border-border" key={r.horizon}>
                <th className={cell}>{r.horizon} 日</th><td className={cell}>{r.evaluable_count}</td><td className={`${cell} ${tone(r.median_return)}`}>{pct(r.median_return)}</td>
                <td className={cell}>{pct(r.positive_return_rate, false)}</td><td className={`${cell} ${tone(r.mean_max_adverse)}`}>{pct(r.mean_max_adverse)}</td><td className={`${cell} ${tone(r.mean_excess_return)}`}>{pct(r.mean_excess_return)}</td>
              </tr>)}</tbody>
            </table>
            {!summary.length && <p className="p-2 text-xs text-muted">此阶段没有触发记录。</p>}
          </div>
        </div>
        <div>
          <div className="flex flex-wrap items-center gap-2"><h3 className="font-medium">每月表现</h3>
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
        {data.stale && <p role="status" className="text-sm text-muted">注意：当前输入与已保存结果不同。收益仍显示原结果；已变化的行情分区不再画 K 线。想更新请重新研究。</p>}
        <details className="text-xs text-muted"><summary className="cursor-pointer">研究限制与数据覆盖</summary>
          <p className="mt-1">版本 {data.rule}。收益基于复权价格，不含真实撮合、费用或滑点；ST 历史推断可能误判。超额收益基准仅为本次样本股票。</p>
          <p>{data.missing_dates.length ? `研究区间缺行情日期：${data.missing_dates.join('、')}` : '研究区间没有缺失的整日分区。'} `—` 表示无可用数值。</p>
        </details>
      </div>
    </details>

    <section className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><h3 className="font-medium">触发案例 · 看个股 K 线</h3>
          <p className="text-xs text-muted">挑一个信号，看看历史上它命中后股价具体怎么走。</p>
        </div>
        <label className="text-sm">股票 <select className="rounded border border-border bg-surface px-2 py-1" value={symbol} onChange={e => {setSymbol(e.target.value); resetCases()}}>
          <option value="">全部股票</option>{data.config.symbols.slice(0, 500).map(code => <option key={code}>{code}</option>)}
        </select></label>
      </div>
      <p className="text-xs text-muted">黄线：触发日 · 蓝线：入场参考日 · 紫线：退出参考日 · 点击放大</p>
      {events.isError ? <p role="alert">触发案例读取失败。 <button className={btn} onClick={() => void events.refetch()}>重试</button></p>
        : events.isLoading ? <p role="status" className="text-sm text-muted">正在读取本页 K 线…</p>
        : rows.length === 0 ? <p className="text-sm text-muted">该条件下没有触发案例。</p>
        : <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {rows.map(row => <button key={caseId(row)} type="button" onClick={() => setSelected(caseId(row))}
            aria-label={`${row.symbol} ${row.signal_date} 放大 K 线`} className="rounded border border-border bg-surface p-2 text-left hover:border-accent">
            <div className="truncate text-sm font-medium">{row.symbol}</div>
            <div className="text-xs text-muted">触发 {row.signal_date} · {statusNames[row.execution_status] ?? '未知'}</div>
            <MiniChart bars={row.bars} spec={{vlines:caseMarks(row, h as Horizon)}} />
            <div className="grid grid-cols-3 gap-1 text-xs tabular-nums">{horizons.map(n => <span key={n} className={tone(row[`return_${n}d`])}>{n} 日 {pct(row[`return_${n}d`])}</span>)}</div>
          </button>)}
        </div>}
      <nav aria-label="触发案例分页" className="flex items-center justify-between border-t border-border py-2 text-xs text-muted">
        <span>共 {total} 条 · 每页 {size} 条</span><div className="flex items-center gap-2">
          <button className={btn} disabled={page <= 1} onClick={() => {setSelected(null); setPage(page - 1)}}>上一页</button>{page}/{pages}
          <button className={btn} disabled={page >= pages} onClick={() => {setSelected(null); setPage(page + 1)}}>下一页</button>
        </div>
      </nav>
    </section>
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
          const hh = h as Horizon
          return {subtitle: `${statusNames[row.execution_status] ?? '未知'} · ${reasons[row.execution_reason] ?? '无判定说明'} · 限价：${source ?? '未知'} · ${h} 日：有利 ${pct(row[`max_favorable_${hh}d`])} / 不利 ${pct(row[`max_adverse_${hh}d`])}`,
            ranges: [], focus: 'all', markers: caseMarks(row, h as Horizon).map(mark => ({...mark, kind:'neutral' as const, above: true}))}
        },
      }} />}
  </section>
}

type Config = { symbols: string[]; signal_ids: string[]; date_from: string; date_to: string; split_date: string }

function StudyConfig({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient()
  const defaults = useQuery({ queryKey: ['sf-default-config'], queryFn: api.signalForwardDefaultConfig, staleTime: 60_000 })
  const pool = useQuery({ queryKey: ['sf-symbols'], queryFn: api.signalForwardSymbols, staleTime: 300_000 })
  const [cfg, setCfg] = useState<Config | null>(null)
  const [allMarket, setAllMarket] = useState(true)
  const [jobId, setJobId] = useState<string | null>(null)
  const [poolQ, setPoolQ] = useState('')

  useEffect(() => {
    if (defaults.data && !cfg) setCfg({
      symbols: [], signal_ids: [...defaults.data.signal_ids],
      date_from: defaults.data.date_from, date_to: defaults.data.date_to, split_date: defaults.data.split_date,
    })
  }, [defaults.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const start = useMutation({
    mutationFn: () => {
      if (!cfg) return Promise.reject(new Error('配置尚未就绪'))
      const symbols = allMarket ? (pool.data?.symbols ?? []).map(s => s.symbol) : cfg.symbols
      return api.signalForwardStart({ ...cfg, symbols })
    },
    onSuccess: (r: ForwardStartResult) => setJobId(r.job_id),
  })
  const job = useQuery({
    queryKey: ['sf-job', jobId], enabled: Boolean(jobId),
    queryFn: () => api.signalForwardStatus(jobId!), refetchInterval: q => {
      const s = (q as unknown as { state?: { data?: SignalForwardJob } }).state?.data?.status
      return s === 'succeeded' || s === 'failed' || s === 'cancelled' ? false : 1200
    },
  })
  useEffect(() => {
    const s = job.data?.status
    if (s === 'succeeded') { qc.invalidateQueries({ queryKey: ['signal-forward-latest'] }); onDone() }
  }, [job.data?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const allSignals = useMemo(() => {
    const base = cfg?.signal_ids ?? []
    return [...new Set([...base, 'boll_squeeze_warning', 'boll_mid_breakdown', 'ma20_bias_over_10'])]
  }, [cfg?.signal_ids])
  const signalNames: Record<string, string> = {
    boll_squeeze_warning: '布林缩口观望', boll_mid_breakdown: '有效下穿布林中轨', ma20_bias_over_10: 'MA20乖离超10%',
  }
  const toggleSignal = (id: string) => setCfg(c => c ? {
    ...c, signal_ids: c.signal_ids.includes(id) ? c.signal_ids.filter(x => x !== id) : [...c.signal_ids, id],
  } : c)
  const filteredPool = useMemo(() => {
    const q = poolQ.trim().toUpperCase()
    const items = pool.data?.symbols ?? []
    return q ? items.filter(s => s.symbol.includes(q) || s.name.includes(poolQ.trim())) : items
  }, [pool.data, poolQ])

  if (defaults.isLoading || !cfg) return <p role="status" className="p-4 text-sm text-muted">正在准备研究配置…</p>
  const running = Boolean(jobId) && !['succeeded', 'failed', 'cancelled'].includes(job.data?.status ?? '')
  return <section className="space-y-3 rounded-lg border border-border bg-surface p-4">
    <div>
      <h2 className="text-base font-semibold">跑一次研究</h2>
      <p className="text-xs text-muted">选好信号和股票范围就能开始。研究在后台跑，结果自动存到本机，下次打开直接用。全市场约 5200 只，可能要几十秒到几分钟。</p>
    </div>
    <div className="flex flex-wrap items-center gap-4">
      <label className="text-sm">研究区间
        <input type="date" className="ml-1 rounded border border-border bg-elevated px-2 py-1" value={cfg.date_from} max={cfg.split_date} onChange={e => setCfg({ ...cfg, date_from: e.target.value })} />
        <span className="mx-1 text-muted">至</span>
        <input type="date" className="rounded border border-border bg-elevated px-2 py-1" value={cfg.date_to} min={cfg.split_date} onChange={e => setCfg({ ...cfg, date_to: e.target.value })} />
      </label>
      <label className="text-sm">验证期起点
        <input type="date" className="ml-1 rounded border border-border bg-elevated px-2 py-1" value={cfg.split_date} min={cfg.date_from} max={cfg.date_to} onChange={e => setCfg({ ...cfg, split_date: e.target.value })} />
      </label>
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm">看哪些信号</span>
      {allSignals.map(id => <label key={id} className="flex items-center gap-1 text-xs">
        <input type="checkbox" checked={cfg.signal_ids.includes(id)} onChange={() => toggleSignal(id)} />
        {signalNames[id] ?? id}
      </label>)}
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={allMarket} onChange={e => setAllMarket(e.target.checked)} />全市场</label>
      {!allMarket && <div className="flex-1">
        <input className="w-full rounded border border-border bg-elevated px-2 py-1 text-xs" placeholder="按代码/名称搜索，勾选加入股票池" value={poolQ} onChange={e => setPoolQ(e.target.value)} />
        <div className="mt-1 max-h-32 overflow-auto rounded border border-border">
          {filteredPool.slice(0, 200).map(s => <label key={s.symbol} className="flex items-center gap-1 px-2 py-0.5 text-xs">
            <input type="checkbox" checked={cfg.symbols.includes(s.symbol)} onChange={() => setCfg(c => c ? {
              ...c, symbols: c.symbols.includes(s.symbol) ? c.symbols.filter(x => x !== s.symbol) : [...c.symbols, s.symbol],
            } : c)} />{s.symbol} {s.name}
          </label>)}
          {!filteredPool.length && <p className="p-2 text-xs text-muted">没有匹配的股票。</p>}
        </div>
        <p className="text-xs text-muted">已选 {cfg.symbols.length} 只。</p>
      </div>}
    </div>
    <div className="flex items-center gap-3">
      <button type="button" className="rounded-btn bg-accent px-4 py-1.5 text-sm text-white disabled:opacity-45"
        disabled={running || start.isPending || cfg.signal_ids.length === 0 || (!allMarket && cfg.symbols.length === 0)}
        onClick={() => start.mutate()}>
        {running ? '研究进行中…' : start.isPending ? '正在提交…' : '启动研究'}
      </button>
      {start.isError && <span className="text-xs text-bull">提交失败：{String(start.error)}</span>}
      {jobId && <span className="text-xs text-muted">
        任务 {jobId} · {running ? `执行中 ${job.data?.progress ?? 0}%` : job.data?.status === 'succeeded' ? '已完成' : job.data?.status === 'failed' ? `失败：${job.data?.error ?? ''}` : '读取中…'}
      </span>}
    </div>
  </section>
}

export function SignalForwardPerformance() {
  const latest = useQuery({ queryKey: ['signal-forward-latest'], queryFn: api.signalForwardLatest })
  const [editing, setEditing] = useState(false)
  return <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
    <div className="flex items-center justify-between">
      <button type="button" className="rounded-btn bg-elevated px-3 py-1.5 text-xs" onClick={() => setEditing(v => !v)}>
        {editing ? '收起配置' : '重新研究 / 配置'}
      </button>
    </div>
    {editing && <StudyConfig onDone={() => setEditing(false)} />}
    {latest.isLoading && <p role="status" className="text-sm text-muted">正在读取触发后表现…</p>}
    {latest.isError && <p role="alert" className="text-sm">读取研究结果失败。 <button className={btn} onClick={() => void latest.refetch()}>重试</button></p>}
    {!latest.isLoading && !latest.isError && !latest.data && !editing && <p className="text-sm text-muted">尚无研究结果，点上方「重新研究 / 配置」启动一次。</p>}
    {latest.data && <Results key={latest.data.run_id} data={latest.data} />}
  </div>
}
