import { IconRiseResearch } from '@/components/BrandIcons'
import { api, type RiseResearchRun } from '@/lib/api'
import { StartCountChart } from '../shared/StartCountChart'
import type {
  DialogDecor, MiniSpec, RecipeSlotProps, ResearchRecord, ResearchRecipe, ResearchPage,
} from '../shared/types'

type Row = RiseResearchRun
const raw = (rec: ResearchRecord) => rec.raw as unknown as Row

const formatSpan = (start: string, end: string) => start === end ? start : `${start} ~ ${end}`
const formatGain = (gain: number) => {
  const pct = gain * 100
  return `+${pct.toFixed(Math.abs(pct) >= 100 ? 0 : 1)}%`
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

function RiseStarts(props: RecipeSlotProps) {
  const { start, end, extra } = props.ctx
  return <StartCountChart {...props} title="连涨启动个数" filters={[extra.maxDays, extra.fullWindow]}
    load={bucket => api.riseResearchStarts({start: start || undefined, end: end || undefined,
      maxDays: Number(extra.maxDays), fullWindow: Boolean(extra.fullWindow), bucket})} />
}

export const riseRecipe: ResearchRecipe = {
  id: 'rise',
  label: '连涨段',
  icon: IconRiseResearch as ResearchRecipe['icon'],

  subtitle: (page: ResearchPage | undefined, ctx) => page?.asOf
    ? `截至 ${page.asOf}。从低点算起，第一次收盘涨过 30% 那天就收尾，中间可以下跌，只保留 ${ctx.extra.maxDays} 个交易日内达标的段，避免混入长期慢涨。缩略图是起点前 ${page.barsBefore ?? 0} 个交易日到达标日后 ${page.barsAfter ?? 0} 个交易日。${ctx.extra.fullWindow ? `当前只看达标日不晚于 ${page.meta?.windowCutoff || '窗口截止日'} 的记录，后面的 K 线已经走完。` : '包含还没走完 30 个交易日的达标段。'}`
    : '从低点算起，第一次收盘涨过 30% 那天就收尾，只保留短时间内达标的区间。',

  sortOptions: [
    { value: 'end_date', label: '结束日' },
    { value: 'gain', label: '涨幅' },
    { value: 'days', label: '天数' },
  ],
  defaultSort: 'end_date',
  initialExtra: { maxDays: 20, fullWindow: false, bucket: 'day', started: '' },
  queryExtra: extra => ({ maxDays: extra.maxDays, fullWindow: extra.fullWindow, started: extra.started }),

  fetchPage: async ctx => {
    const data = await api.riseResearch({
      start: ctx.start || undefined,
      end: ctx.end || undefined,
      maxDays: Number(ctx.extra.maxDays),
      q: ctx.q,
      sort: ctx.sort as 'end_date' | 'gain' | 'days',
      order: ctx.order,
      offset: ctx.offset,
      limit: ctx.limit,
      fullWindow: Boolean(ctx.extra.fullWindow),
      started: (ctx.extra.started as string) || undefined,
    })
    return {
      rows: data.rows.map(row => ({
        id: row.id, symbol: row.symbol, name: row.name,
        startDate: row.start_date, endDate: row.end_date,
        bars: row.bars, raw: row as unknown as Record<string, unknown>,
      })),
      total: data.total,
      computed: data.computed !== false,
      asOf: data.as_of,
      barsBefore: data.bars_before,
      barsAfter: data.bars_after,
      meta: { fullWindow: data.full_window, windowCutoff: data.window_cutoff },
    }
  },

  fetchZoom: async rec => {
    const data = await api.riseResearchZoom({ symbol: rec.symbol, start: rec.startDate, end: rec.endDate })
    return {
      bars: data.bars, fromDate: data.from_date, toDate: data.to_date,
      barsBefore: data.bars_before, barsAfter: data.bars_after,
    }
  },

  ExtraFilters: ({ ctx, setExtra }) => (
    <>
      <label className="flex items-center gap-1 text-xs text-muted">
        达标用时
        <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={Number(ctx.extra.maxDays)} onChange={event => setExtra({ maxDays: Number(event.target.value) })}>
          <option value={10}>不超过 10 天</option>
          <option value={15}>不超过 15 天</option>
          <option value={20}>不超过 20 天</option>
          <option value={30}>不超过 30 天</option>
        </select>
      </label>
      <button type="button" className="h-8 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => setExtra({ fullWindow: !ctx.extra.fullWindow })}>
        {ctx.extra.fullWindow ? '已满30日' : '包含最新'}
      </button>
    </>
  ),

  ExtraPanel: RiseStarts,

  card: rec => {
    const row = raw(rec)
    const sides = barSides(rec.bars, rec.startDate, rec.endDate)
    return (
      <>
        <div className="flex items-baseline justify-between gap-2">
          <div className="min-w-0 truncate text-sm font-medium text-foreground">{row.name || row.symbol}</div>
          <div className="shrink-0 text-sm text-bull">{formatGain(row.gain)} · {row.days} 天</div>
        </div>
        <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-muted">
          <span className="truncate font-mono">{row.symbol}</span>
          <span>{formatSpan(rec.startDate, rec.endDate)}</span>
        </div>
        <div className="mt-1 text-[11px] text-muted">涨前 {row.base_close.toFixed(2)} → 结束 {row.end_close.toFixed(2)} · 前 {sides.before} 根 · 后 {sides.after} 根</div>
      </>
    )
  },

  mini: rec => ({
    band: { start: rec.startDate, end: rec.endDate },
    shadeAfter: rec.endDate,
  } as MiniSpec),

  dialogDecor: (rec, _ctx, frame): DialogDecor => {
    const row = raw(rec)
    const gainText = formatGain(row.gain)
    return {
      subtitle: `${formatSpan(rec.startDate, rec.endDate)} · ${gainText} · ${row.days} 天 · 涨前收盘 ${row.base_close.toFixed(2)} → 结束收盘 ${row.end_close.toFixed(2)}`,
      ranges: [{ start: rec.startDate, end: rec.endDate, label: gainText, color: 'rgba(199,64,64,0.42)' }],
      markers: rec.startDate === rec.endDate
        ? [{ date: rec.startDate, kind: 'neutral', above: true, label: gainText, color: '#FACC15' }]
        : [
            { date: rec.startDate, kind: 'neutral', above: true, label: '起涨', color: '#FACC15' },
            { date: rec.endDate, kind: 'neutral', above: true, label: gainText, color: '#FACC15' },
          ],
      focus: frame === 'year' ? 'all' : { start: rec.startDate, end: rec.endDate, padding: 20 },
      chartKey: frame,
    }
  },

  frames: {
    spanLabel: '看这段', allLabel: '看全年',
    spanText: '当前对准这段上涨。', allText: '当前是全年，红色区间是这段上涨。',
  },
  zoomNouns: { beforeWindow: '起涨前一年', spanNoun: '', afterWindow: '一年' },

  empty: ctx => ({
    title: ctx.extra.started ? `${ctx.extra.started} 没有启动的达标段` : '这个条件下没有达标段',
    hint: ctx.extra.started ? '再点一次这根柱子，或点清除选择。' : `放宽日期或增加达标用时的天数后再看。只保留 ${ctx.extra.maxDays} 个交易日内第一次收盘涨过 30% 的段。`,
  }),
  notComputed: { title: '还没有计算，请点重新计算', hint: '算完会保存下来。之后打开页面直接读取，不会因为行情更新自动重算。' },

  rebuild: {
    label: '重新计算',
    pendingLabel: '正在重新划分…',
    run: async () => { await api.riseResearchRebuild() },
  },
  invalidateKeys: [['rise-research']],
}
