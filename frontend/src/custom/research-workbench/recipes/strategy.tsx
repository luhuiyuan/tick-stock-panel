import { useQuery } from '@tanstack/react-query'
import { BarChart3 } from 'lucide-react'
import { api, type StrategyReviewRun, type StrategyReviewStat, type StrategyReviewMeta } from '@/lib/api'
import type {
  DialogDecor, MiniSpec, RecipeSlotProps, ResearchJobView,
  ResearchRecord, ResearchRecipe, ResearchPage,
} from '../shared/types'

type Row = StrategyReviewRun
type Horizon = 1 | 3 | 5
const HORIZONS: Horizon[] = [1, 3, 5]
const raw = (rec: ResearchRecord) => rec.raw as unknown as Row

const fmtPct = (value: number | null | undefined) => {
  if (value === null || value === undefined) return '—'
  const pct = value * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`
}
function pick(row: Row, horizon: number, field: 'ret' | 'status' | 'exit_date') {
  return row[`${field}_${horizon}` as keyof Row]
}
const horizonOf = (extra: Record<string, unknown>): Horizon => {
  const h = Number(extra.horizon)
  return (HORIZONS as number[]).includes(h) ? h as Horizon : 1
}

/** 策略清单在筛选器与统计卡之间共用同一份 react-query 缓存 */
function usePresets(keyPrefix: readonly unknown[]) {
  const query = useQuery({
    queryKey: [...keyPrefix, 'strategies'],
    queryFn: () => api.strategyReviewStrategies(),
    staleTime: 5 * 60_000,
  })
  return { presets: (query.data?.presets ?? []).filter(p => p.execution_available), isError: query.isError }
}

function ExtraFilters({ ctx, setExtra, keyPrefix }: RecipeSlotProps) {
  const { presets, isError } = usePresets(keyPrefix)
  const ids = (ctx.extra.strategyIds as string[]) ?? []
  const result = (ctx.extra.result as string) ?? 'all'
  const strategyId = (ctx.extra.strategyId as string) ?? ''
  return (
    <>
      <button
        type="button"
        className="inline-flex h-8 items-center rounded-btn bg-elevated px-2.5 text-xs text-secondary hover:text-foreground"
        onClick={() => setExtra({ showStrategies: !ctx.extra.showStrategies })}
      >
        策略 · 已选 {ids.length} 个 {ctx.extra.showStrategies ? '▲' : '▼'}
      </button>
      <label className="flex items-center gap-1 text-xs text-muted">
        成本%
        <input type="number" step="0.01" min="0" max="1" value={String(ctx.extra.costPct ?? '0.15')}
          onChange={e => setExtra({ costPct: e.target.value })}
          className="h-8 w-16 rounded-btn bg-elevated px-2 text-xs text-foreground outline-none" />
      </label>
      <label className="flex items-center gap-1 text-xs text-muted">
        结果
        <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={result} onChange={e => setExtra({ result: e.target.value })}>
          <option value="all">全部</option>
          <option value="win">盈利</option>
          <option value="lose">失败</option>
          <option value="pending">观察中</option>
        </select>
      </label>
      <label className="flex items-center gap-1 text-xs text-muted">
        策略
        <select className="h-8 max-w-[150px] rounded-btn bg-elevated px-2 text-foreground" value={strategyId} onChange={e => setExtra({ strategyId: e.target.value })}>
          <option value="">不限</option>
          {presets.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      <label className="flex items-center gap-1 text-xs text-muted">
        收益% 从
        <input type="number" step="0.5" value={String(ctx.extra.minRet ?? '')} onChange={e => setExtra({ minRet: e.target.value })} placeholder="不限" className="h-8 w-16 rounded-btn bg-elevated px-2 text-xs text-foreground outline-none" />
        到
        <input type="number" step="0.5" value={String(ctx.extra.maxRet ?? '')} onChange={e => setExtra({ maxRet: e.target.value })} placeholder="不限" className="h-8 w-16 rounded-btn bg-elevated px-2 text-xs text-foreground outline-none" />
      </label>
      {isError && <span className="text-xs text-danger">策略清单读取失败</span>}
    </>
  )
}

/** 策略复盘自有的小部件: 展开的策略清单 + 1/3/5 日胜率统计卡 */
function ExtraPanel({ ctx, setExtra, page, keyPrefix }: RecipeSlotProps) {
  const { presets } = usePresets(keyPrefix)
  const ids = (ctx.extra.strategyIds as string[]) ?? []
  const horizon = horizonOf(ctx.extra)
  const stats = (page?.meta?.stats ?? {}) as Record<string, StrategyReviewStat>
  return (
    <>
      {ctx.extra.showStrategies && (
        <div className="border-b border-border bg-surface/60 px-5 py-2">
          <div className="mb-2 flex items-center gap-2 text-xs text-muted">
            <span>只列出可执行的日线选股策略（共 {presets.length} 个）。建议一次选 2-5 个、区间不超过 60 个交易日；一次全选可能因个别策略计算过慢而长时间无响应。</span>
            <button type="button" className="h-7 rounded-btn bg-elevated px-2 hover:text-foreground" onClick={() => setExtra({ strategyIds: presets.map(p => p.id) })}>全选</button>
            <button type="button" className="h-7 rounded-btn bg-elevated px-2 hover:text-foreground" onClick={() => setExtra({ strategyIds: [] })}>清空</button>
          </div>
          <div className="grid max-h-52 grid-cols-1 gap-1 overflow-auto sm:grid-cols-2 xl:grid-cols-3">
            {presets.map(preset => (
              <label key={preset.id} className="flex cursor-pointer items-center gap-1.5 rounded px-1 py-0.5 text-xs text-secondary hover:bg-elevated">
                <input type="checkbox" checked={ids.includes(preset.id)}
                  onChange={event => setExtra({ strategyIds: event.target.checked ? [...ids, preset.id] : ids.filter(id => id !== preset.id) })} />
                <span className="truncate" title={preset.description || preset.id}>{preset.name}</span>
                <span className="shrink-0 font-mono text-[10px] text-muted">{preset.id}</span>
              </label>
            ))}
          </div>
        </div>
      )}
      <div className="grid grid-cols-1 gap-2 border-b border-border px-5 py-2 sm:grid-cols-3">
        {HORIZONS.map(h => {
          const stat = stats[String(h)]
          const active = horizon === h
          return (
            <button key={h} type="button" onClick={() => setExtra({ horizon: h })}
              className={`rounded-lg border px-3 py-2 text-left transition-colors ${active ? 'border-accent/60 bg-elevated' : 'border-border bg-surface hover:border-accent/30'}`}>
              <div className="flex items-baseline justify-between">
                <span className="text-xs text-secondary">持有 {h} 个交易日{active ? ' · 当前' : ''}</span>
                <span className={`text-lg font-semibold ${stat?.win_rate == null ? 'text-muted' : stat.win_rate >= 0.5 ? 'text-bull' : 'text-bear'}`}>
                  {stat?.win_rate != null ? `${(stat.win_rate * 100).toFixed(1)}%` : '—'}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted">
                <span>平均 <b className={stat?.avg_ret != null && stat.avg_ret >= 0 ? 'text-bull' : 'text-bear'}>{fmtPct(stat?.avg_ret)}</b></span>
                <span>中位 <b className={stat?.median_ret != null && stat.median_ret >= 0 ? 'text-bull' : 'text-bear'}>{fmtPct(stat?.median_ret)}</b></span>
                <span>盈亏比 {stat?.profit_loss_ratio != null ? stat.profit_loss_ratio.toFixed(2) : '—'}</span>
              </div>
              <div className="mt-0.5 text-[11px] text-muted">{stat?.win ?? 0} 赢 / {stat?.lose ?? 0} 输 / {stat?.pending ?? 0} 观察中 · 有效样本 {stat?.total ?? 0}</div>
            </button>
          )
        })}
      </div>
    </>
  )
}

export const strategyRecipe: ResearchRecipe = {
  id: 'strategy',
  label: '策略信号',
  icon: BarChart3,

  subtitle: (page: ResearchPage | undefined) => {
    const meta = (page?.meta?.raw ?? {}) as StrategyReviewMeta
    return meta.start
      ? `区间 ${meta.start} ~ ${meta.end} · ${meta.strategy_ids?.length ?? 0} 个策略 · ${meta.scan_days ?? 0} 个交易日 · 命中 ${meta.signals ?? 0} 条。买入按信号日收盘，卖出按第 1/3/5 个交易日收盘，扣双边成本 ${((meta.cost_pct ?? 0.0015) * 100).toFixed(2)}%。缩略图前 ${page?.barsBefore ?? 20} 根后 ${page?.barsAfter ?? 10} 根。结果使用信号日之后的行情计算，属于历史复盘，不构成实盘信号。`
      : '指定策略在历史区间逐日选股，看 K 线缩略图与后续 1/3/5 日盈利统计。不填日期默认最近 120 个交易日。结果含未来数据，仅用于复盘。'
  },

  sortOptions: [
    { value: 'signal_date', label: '信号日' },
    { value: 'ret_1', label: '1日收益' },
    { value: 'ret_3', label: '3日收益' },
    { value: 'ret_5', label: '5日收益' },
    { value: 'strategy_count', label: '命中策略数' },
  ],
  defaultSort: 'signal_date',
  initialExtra: {
    strategyIds: [], showStrategies: false, costPct: '0.15', horizon: 1,
    result: 'all', strategyId: '', minRet: '', maxRet: '',
  },
  queryExtra: extra => ({
    horizon: extra.horizon, result: extra.result, strategyId: extra.strategyId,
    minRet: extra.minRet, maxRet: extra.maxRet,
  }),

  fetchPage: async ctx => {
    const h = horizonOf(ctx.extra)
    const data = await api.strategyReviewRuns({
      horizon: h,
      result: (ctx.extra.result as 'all' | 'win' | 'lose' | 'pending') ?? 'all',
      start: ctx.start || undefined,
      end: ctx.end || undefined,
      q: ctx.q,
      strategyId: (ctx.extra.strategyId as string) || undefined,
      minRet: ctx.extra.minRet === '' || ctx.extra.minRet == null ? undefined : Number(ctx.extra.minRet) / 100,
      maxRet: ctx.extra.maxRet === '' || ctx.extra.maxRet == null ? undefined : Number(ctx.extra.maxRet) / 100,
      sort: ctx.sort as 'signal_date' | 'strategy_count' | 'ret_1' | 'ret_3' | 'ret_5',
      order: ctx.order,
      offset: ctx.offset,
      limit: ctx.limit,
    })
    return {
      rows: data.rows.map(row => ({
        id: row.id, symbol: row.symbol, name: row.name,
        startDate: row.signal_date, endDate: row.signal_date,
        bars: row.bars, raw: row as unknown as Record<string, unknown>,
      })),
      total: data.total,
      computed: data.computed !== false,
      asOf: data.meta?.as_of,
      barsBefore: data.bars_before,
      barsAfter: data.bars_after,
      meta: { raw: data.meta, stats: data.stats },
    }
  },

  fetchZoom: async rec => {
    const data = await api.strategyReviewZoom({
      symbol: rec.symbol, start: rec.startDate, end: rec.startDate,
    })
    return {
      bars: data.bars, fromDate: data.from_date, toDate: data.to_date,
      barsBefore: 0, barsAfter: 0,
    }
  },

  ExtraFilters,
  ExtraPanel,

  card: (rec, ctx) => {
    const row = raw(rec)
    const h = horizonOf(ctx.extra)
    const ret = pick(row, h, 'ret') as number | null
    const status = pick(row, h, 'status') as string
    const exitDate = pick(row, h, 'exit_date') as string | null
    const badge = status === 'win' ? '盈利' : status === 'lose' ? '失败' : '观察中'
    return (
      <>
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
      </>
    )
  },

  mini: (rec, ctx) => {
    const row = raw(rec)
    const h = horizonOf(ctx.extra)
    const exitDate = pick(row, h, 'exit_date') as string | null
    const vlines = [{ date: row.signal_date, color: '#E2B15A', dash: '3 2' }]
    if (exitDate) vlines.push({ date: exitDate, color: '#8A8A8A', dash: '2 2' })
    return { shadeAfter: row.signal_date, shadeAfterInclusive: true, vlines } as MiniSpec
  },

  dialogDecor: (rec, ctx): DialogDecor => {
    const row = raw(rec)
    const h = horizonOf(ctx.extra)
    const exitDate = pick(row, h, 'exit_date') as string | null
    const ret = pick(row, h, 'ret') as number | null
    return {
      subtitle: `信号日 ${row.signal_date} · 买入收盘 ${row.entry_close?.toFixed(2) ?? '—'} · 持有 ${h} 日 ${fmtPct(ret)} · 命中 ${row.strategy_count} 个策略${row.tradable === 'locked' ? ' · 信号日一字板/停牌' : ''}`,
      ranges: exitDate ? [{ start: row.signal_date, end: exitDate, label: `持有 ${h} 日 ${fmtPct(ret)}`, color: 'rgba(226,177,90,0.24)' }] : [],
      markers: [
        { date: row.signal_date, kind: 'neutral', above: true, label: '买入', color: '#E2B15A' },
        ...(exitDate ? [{ date: exitDate, kind: 'neutral' as const, above: true, label: `卖出 ${fmtPct(ret)}`, color: '#9CA3AF' }] : []),
      ],
      focus: { start: row.signal_date, end: exitDate ?? row.signal_date, padding: 20 },
      chartKey: String(h),
    }
  },

  zoomNouns: { beforeWindow: '信号前一年', spanNoun: '当前对准买入到卖出。', afterWindow: '一年' },

  empty: () => ({ title: '这个条件下没有信号', hint: '放宽结果类型、收益区间或日期范围，或重新运行一次选股。' }),
  notComputed: { title: '还没有运行过选股', hint: '在上面选好策略与日期区间，点「运行选股」。算完会保存下来，之后打开页面直接读取。' },

  rebuild: {
    label: '运行选股',
    pendingLabel: '正在扫描…',
    canRun: ctx => ((ctx.extra.strategyIds as string[]) ?? []).length > 0,
    run: async ctx => {
      const data = await api.strategyReviewRebuild({
        strategyIds: (ctx.extra.strategyIds as string[]) ?? [],
        start: ctx.start || undefined,
        end: ctx.end || undefined,
        costPct: Number(ctx.extra.costPct ?? '0.15') / 100,
      })
      return { jobId: data.job_id }
    },
  },
  job: {
    poll: async (jobId): Promise<ResearchJobView> => {
      const j = await api.strategyReviewJob(jobId)
      const done = j.status === 'succeeded' || j.status === 'failed'
      const lastLog = j.log?.[j.log.length - 1]?.msg ?? ''
      return {
        done,
        failed: j.status === 'failed',
        node: (
          <div>
            <div className="flex items-center justify-between text-xs text-secondary">
              <span>{j.status === 'failed' ? '扫描失败' : j.stage === 'init' ? '等待开始…' : lastLog || '正在扫描…'}</span>
              <span>{j.progress}%</span>
            </div>
            <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-elevated">
              <div className="h-full bg-accent transition-all" style={{ width: `${j.progress}%` }} />
            </div>
            {j.status === 'failed' && <div className="mt-1 text-xs text-danger">{j.error}</div>}
          </div>
        ),
      }
    },
  },
  invalidateKeys: [['strategy-review']],
}
