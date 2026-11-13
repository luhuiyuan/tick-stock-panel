import { IconLimitResearch } from '@/components/BrandIcons'
import { api, type LimitUpResearchStreak } from '@/lib/api'
import type {
  DialogDecor, MiniSpec, ResearchRecord, ResearchRecipe, ResearchPage,
} from '../shared/types'

type Row = LimitUpResearchStreak
const raw = (rec: ResearchRecord) => rec.raw as unknown as Row

const formatSpan = (start: string, end: string) => start === end ? start : `${start} ~ ${end}`

function barSides(bars: { date: string }[], start: string, end: string) {
  let before = 0
  let after = 0
  for (const bar of bars) {
    if (bar.date < start) before += 1
    else if (bar.date > end) after += 1
  }
  return { before, after }
}

export const limitUpRecipe: ResearchRecipe = {
  id: 'limit-up',
  label: '涨停段',
  icon: IconLimitResearch as ResearchRecipe['icon'],

  subtitle: (page: ResearchPage | undefined) => page?.asOf
    ? `截至 ${page.asOf}。缩略图是首板前 ${page.barsBefore ?? 0} 个交易日到结束后 ${page.barsAfter ?? 0} 个交易日。${(page.meta?.fullWindow as boolean) ? `当前只看结束日不晚于 ${page.meta?.windowCutoff || '窗口截止日'} 的记录，后面的 K 线已经走完。` : '包含刚结束的连板；这些记录后面还没有 K 线。'}`
    : '一段连续涨停算一条。',

  sortOptions: [
    { value: 'end_date', label: '结束日' },
    { value: 'boards', label: '连板数' },
  ],
  defaultSort: 'end_date',
  initialExtra: { minBoards: 1, fullWindow: false },
  queryExtra: extra => ({ minBoards: extra.minBoards, fullWindow: extra.fullWindow }),

  fetchPage: async ctx => {
    const data = await api.limitUpResearch({
      start: ctx.start || undefined,
      end: ctx.end || undefined,
      minBoards: Number(ctx.extra.minBoards),
      q: ctx.q,
      sort: ctx.sort as 'end_date' | 'boards',
      order: ctx.order,
      offset: ctx.offset,
      limit: ctx.limit,
      fullWindow: Boolean(ctx.extra.fullWindow),
    })
    return {
      rows: data.rows.map(row => ({
        id: row.id,
        symbol: row.symbol,
        name: row.name,
        startDate: row.start_date,
        endDate: row.end_date,
        bars: row.bars,
        raw: row as unknown as Record<string, unknown>,
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
    const data = await api.limitUpResearchZoom({
      symbol: rec.symbol, start: rec.startDate, end: rec.endDate,
    })
    return {
      bars: data.bars,
      fromDate: data.from_date,
      toDate: data.to_date,
      barsBefore: data.bars_before,
      barsAfter: data.bars_after,
    }
  },

  ExtraFilters: ({ ctx, setExtra }) => (
    <>
      <label className="flex items-center gap-1 text-xs text-muted">
        最少连板
        <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={Number(ctx.extra.minBoards)} onChange={event => setExtra({ minBoards: Number(event.target.value) })}>
          {[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value}</option>)}
        </select>
      </label>
      <button type="button" className="h-8 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => setExtra({ fullWindow: !ctx.extra.fullWindow })}>
        {ctx.extra.fullWindow ? '已满30日' : '包含最新'}
      </button>
    </>
  ),

  card: rec => {
    const row = raw(rec)
    const sides = barSides(rec.bars, rec.startDate, rec.endDate)
    return (
      <>
        <div className="flex items-baseline justify-between gap-2">
          <div className="min-w-0 truncate text-sm font-medium text-foreground">{row.name || row.symbol}</div>
          <div className="shrink-0 text-sm text-bull">{row.boards} 连板</div>
        </div>
        <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-muted">
          <span className="truncate font-mono">{row.symbol}</span>
          <span>{formatSpan(rec.startDate, rec.endDate)}</span>
        </div>
        {row.days !== row.boards && (
          <div className="mt-1 text-[11px] text-muted">区间内看到 {row.days} 个涨停日，连板数是 {row.boards}</div>
        )}
        <div className="mt-1 text-[11px] text-muted">前 {sides.before} 根 · 后 {sides.after} 根</div>
      </>
    )
  },

  mini: rec => ({
    band: { start: rec.startDate, end: rec.endDate },
    shadeAfter: rec.endDate,
  } as MiniSpec),

  dialogDecor: (rec, _ctx, frame): DialogDecor => {
    const row = raw(rec)
    return {
      subtitle: `${formatSpan(rec.startDate, rec.endDate)} · ${row.boards} 连板`,
      ranges: [{ start: rec.startDate, end: rec.endDate, label: `${row.boards}连板`, color: 'rgba(199,64,64,0.42)' }],
      markers: rec.startDate === rec.endDate
        ? [{ date: rec.startDate, kind: 'neutral', above: true, label: `${row.boards}连板`, color: '#FACC15' }]
        : [
            { date: rec.startDate, kind: 'neutral', above: true, label: '首板', color: '#FACC15' },
            { date: rec.endDate, kind: 'neutral', above: true, label: `${row.boards}连板`, color: '#FACC15' },
          ],
      focus: frame === 'year' ? 'all' : { start: rec.startDate, end: rec.endDate, padding: 20 },
      chartKey: frame,
    }
  },

  frames: {
    spanLabel: '看连板', allLabel: '看全年',
    spanText: '当前对准这段涨停。', allText: '当前是全年，红色区间是这段涨停。',
  },
  zoomNouns: { beforeWindow: '首板前一年', spanNoun: '', afterWindow: '一年' },

  empty: () => ({ title: '这个条件下没有连板记录', hint: '放宽日期或连板数后再看。只涨停一天也会单独成一条。' }),
  notComputed: { title: '还没有计算，请点重新计算', hint: '算完会保存下来。之后打开页面直接读取，不会因为行情更新自动重算。' },

  rebuild: {
    label: '重新计算',
    pendingLabel: '正在重新整理…',
    run: async () => { await api.limitUpResearchRebuild() },
  },
  invalidateKeys: [['limit-up-research']],
}
