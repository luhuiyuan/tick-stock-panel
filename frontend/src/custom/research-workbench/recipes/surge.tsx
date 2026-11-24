import { api } from '@/lib/api'
import { StartCountChart } from '../shared/StartCountChart'
import type { ResearchCtx, ResearchRecipe } from '../shared/types'
import { riseRecipe } from './rise'

import { SurgeCoverage } from '../SurgeCoverage'
import { SignalForwardPerformance } from '../SignalForwardPerformance'

function filters({ start, end, q, extra }: ResearchCtx) {
  const params = new URLSearchParams({ pending: String(Boolean(extra.pending)), q })
  if (start) params.set('start', start)
  if (end) params.set('end', end)
  return params
}
const pct = (v: unknown) => `+${(100 * Number(v)).toFixed(2)}%`

export const surgeRecipe: ResearchRecipe = {
  AlternateView: SurgeCoverage, PerformanceView: SignalForwardPerformance, primaryLabel: '猛拉记录', alternateLabel: '信号覆盖榜', performanceLabel: '触发后表现',
  id: 'surge', label: '短期猛拉', icon: riseRecipe.icon,
  subtitle: page => `${page?.asOf ? `计算截至 ${page.asOf}。` : ''}单日涨幅 ≥3% 起算；允许停顿一天（收盘回撤 ≤5%），次日须涨 ≥3% 且创新高。累计 ≥15%、日均复合 ≥3% 入选。日期按起始日筛选；起始日是事后归属，不是当日信号。`,
  initialExtra: { pending: false, bucket: 'day', started: '' },
  queryExtra: extra => ({ pending: extra.pending, started: extra.started }),
  defaultSort: 'end_date',
  sortOptions: [{value: 'end_date', label: '结束日'}, {value: 'gain', label: '累计涨幅'}, {value: 'daily_gain', label: '日均复合涨幅'}],
  fetchPage: async ctx => {
    const p = filters(ctx)
    for (const [k, v] of Object.entries({ started: ctx.extra.started, sort: ctx.sort, order: ctx.order, offset: ctx.offset, limit: ctx.limit })) p.set(k, String(v))
    const page = await api.surgeRuns(p)
    return { computed: page.computed, total: page.total, asOf: page.as_of, rows: page.rows.map(row => ({
      id: row.id, symbol: row.symbol, name: row.name, bars: row.bars, startDate: row.start_date, endDate: row.end_date, raw: {...row},
    })) }
  },
  ExtraFilters: ({ctx, setExtra}) => <label>状态 <select value={ctx.extra.pending ? 'pending' : 'ended'}
    onChange={e => setExtra({pending: e.target.value === 'pending', started: ''})}>
    <option value="ended">已结束</option><option value="pending">待确认</option></select></label>,
  ExtraPanel: props => <StartCountChart {...props} title="猛拉段起始日分布" filters={[props.ctx.extra.pending, props.ctx.q]}
    load={bucket => { const p = filters(props.ctx); p.set('bucket', bucket); return api.surgeStarts(p) }} />,
  card: r => <>
    <div className="text-sm font-medium">{r.name || r.symbol} · {r.raw.pending ? '待确认' : '已结束'}</div>
    <div className="text-xs text-muted">{r.symbol} · {r.startDate} 至 {r.endDate}</div>
    <div className="text-xs text-bull">{Number(r.raw.days)} 个交易日 · 累计 {pct(r.raw.gain)} · 日均 {pct(r.raw.daily_gain)}</div>
  </>,
  mini: riseRecipe.mini, dialogDecor: riseRecipe.dialogDecor, fetchZoom: riseRecipe.fetchZoom,
  frames: riseRecipe.frames, zoomNouns: riseRecipe.zoomNouns,
  empty: () => ({title: '当前条件下没有猛拉段', hint: '可切换状态、清除起始日选择或放宽筛选日期。'}),
  notComputed: {title: '尚未计算短期猛拉', hint: '只有点击重新计算才会扫描日线；不会覆盖连涨段结果。'},
  rebuild: {label: '重新计算', pendingLabel: '正在识别猛拉段…', run: async () => { await api.surgeRebuild() }},
}
