import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, Info, Search } from 'lucide-react'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import type { ChartMarker, ChartRange, OHLC } from '@/components/EChartsCandlestick'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { PageHeader } from '@/components/PageHeader'
import { api } from '@/lib/api'
import type {
  PhaseLabDay,
  PhaseLabStructure,
  PhaseLabTimeline,
} from '@/lib/api'

const LOOKBACKS = [60, 250, 500, 1000] as const
type Lookback = typeof LOOKBACKS[number]
type RuleMode = 'all' | 'effective'
type ViewMode = 'structure' | 'signals'

/**
 * 「主力阶段」配色刻意用中性色（蓝/紫/橙/灰），不与 A 股「红涨绿跌」混淆。
 * 阶段字符串来自上游 _pattern_phase，含 emoji 后缀，按关键字归类。
 * 仅用于 signals（旧）视图。
 */
const TONES = {
  accum: { color: '#3B82F6', label: '吸筹' },
  shakeout: { color: '#8B5CF6', label: '试盘 / 洗盘' },
  markup: { color: '#F97316', label: '突破 / 拉升' },
  distribution: { color: '#94A3B8', label: '出货 / 风控' },
  neutral: { color: '#6B7280', label: '观望' },
} as const
type ToneKey = keyof typeof TONES

/** structure 视图的方向色：客观涨跌，遵循 A 股「红涨绿跌」，低饱和避免刺眼。 */
const DIR = {
  UP: { area: 'rgba(239,68,68,0.10)', band: '#F87171', text: '#FCA5A5', label: '上涨' },
  DOWN: { area: 'rgba(34,197,94,0.10)', band: '#4ADE80', text: '#86EFAC', label: '下跌' },
  SIDEWAYS: { area: 'rgba(148,163,184,0.10)', band: '#94A3B8', text: '#CBD5E1', label: '横盘' },
} as const

function toneKeyOf(phase: string): ToneKey {
  if (phase.includes('出货')) return 'distribution'
  if (phase.includes('突破') || phase.includes('拉升')) return 'markup'
  if (phase.includes('洗盘') || phase.includes('试盘')) return 'shakeout'
  if (phase.includes('吸筹')) return 'accum'
  return 'neutral'
}

function toneOf(phase: string) {
  return TONES[toneKeyOf(phase)]
}

function shortCodes(day: PhaseLabDay) {
  const codes = day.fired.map(f => f.code)
  if (codes.length <= 2) return codes.join('/')
  return `${codes.slice(0, 2).join('/')}+${codes.length - 2}`
}

/** 走势结构视图的右列：逐段明细，供肉眼核对分段与变点是否贴合真实走势。 */
function SegmentTable({ data }: { data: PhaseLabStructure }) {
  const segments = data.ground_truth.segments
  const startCloseOf = (index: number) => data.bars[index]?.close
  return (
    <div className="flex min-w-0 flex-col rounded-md border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs">
        <Activity className="h-3.5 w-3.5 text-muted" />
        <span className="font-medium text-foreground">分段明细</span>
        <span className="text-muted">{segments.length} 段 · 变点 {data.ground_truth.bkps.length} 个</span>
      </div>
      <div className="max-h-[524px] min-h-0 overflow-auto">
        <table className="w-full border-collapse text-[11px]">
          <thead className="sticky top-0 bg-elevated/95 text-[10px] text-muted backdrop-blur">
            <tr>
              <th className="px-2 py-1.5 text-left font-medium">#</th>
              <th className="px-2 py-1.5 text-left font-medium">方向</th>
              <th className="px-2 py-1.5 text-left font-medium">起止日期</th>
              <th className="px-2 py-1.5 text-right font-medium">交易日</th>
              <th className="px-2 py-1.5 text-right font-medium">首 → 末收盘</th>
              <th className="px-2 py-1.5 text-right font-medium">涨跌</th>
            </tr>
          </thead>
          <tbody>
            {segments.map((s, i) => {
              const dir = DIR[s.direction]
              const first = startCloseOf(s.start)
              const last = startCloseOf(s.end)
              return (
                <tr key={`${s.start}-${i}`} className="border-t border-border/60 hover:bg-elevated/40">
                  <td className="px-2 py-1.5 font-mono text-muted">{i + 1}</td>
                  <td className="px-2 py-1.5">
                    <span className="inline-flex items-center gap-1" style={{ color: dir.text }}>
                      <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: dir.band }} />
                      {dir.label}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 font-mono text-secondary">
                    {s.from} ~ {s.to}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-foreground">{s.end - s.start + 1}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right font-mono text-secondary">
                    {first == null ? '—' : first.toFixed(2)} → {last == null ? '—' : last.toFixed(2)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono" style={{ color: dir.text }}>
                    {s.change_pct > 0 ? '+' : ''}{s.change_pct.toFixed(2)}%
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <details className="m-2 rounded-md border border-border px-2 py-1.5 text-[10px] leading-relaxed text-muted">
          <summary className="cursor-pointer text-foreground/80">口径与免责说明（{data.notes.length} 条）</summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-4">
            {data.notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </details>
      </div>
    </div>
  )
}

export function PhaseLabPage() {
  const [input, setInput] = useState('000006.SZ')
  const [symbol, setSymbol] = useState('000006.SZ')
  const [lookback, setLookback] = useState<Lookback>(250)
  const [rules, setRules] = useState<RuleMode>('all')
  const [mode, setMode] = useState<ViewMode>('structure')
  const [pen, setPen] = useState(0.05)
  const [suggestions, setSuggestions] = useState<{ symbol: string; name: string }[]>([])
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  // 悬浮阶段色标时的预览高亮，优先级高于点击选中；移出即恢复（signals 视图）
  const [hoverTone, setHoverTone] = useState<ToneKey | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // 旧「形态信号」视图：只在切过去时才请求（首屏不加载，避免逐日重放）
  const query = useQuery({
    queryKey: ['phase-lab', symbol, lookback, rules],
    queryFn: () => api.phaseLabTimeline({ symbol, lookback, rules }),
    enabled: !!symbol && mode === 'signals',
    staleTime: 30_000,
  })

  // 首屏「走势结构」：只用未来数据算一次离线最优分割，不逐日重放、不评分
  const structureQuery = useQuery({
    queryKey: ['phase-lab-structure', symbol, lookback, pen],
    queryFn: () => api.phaseLabStructure({ symbol, lookback, pen }),
    enabled: !!symbol && mode === 'structure',
    staleTime: 60_000,
  })

  // 输入联想：复用现有标的搜索接口，选中即带出带交易所后缀的完整代码
  useEffect(() => {
    const q = input.trim()
    if (!q || q.toUpperCase() === symbol) {
      setSuggestions([])
      return
    }
    const timer = window.setTimeout(() => {
      api.instrumentSearch(q, 8)
        .then(res => setSuggestions((res.results ?? []).map(r => ({ symbol: r.symbol, name: r.name }))))
        .catch(() => setSuggestions([]))
    }, 250)
    return () => window.clearTimeout(timer)
  }, [input, symbol])

  const resetSelection = () => {
    setSelectedDate(null)
    setHoverTone(null)
  }

  const submit = () => {
    const next = input.trim().toUpperCase()
    if (!next) return
    setSymbol(next)
    setSuggestions([])
    resetSelection()
  }

  const result: PhaseLabTimeline | undefined = query.data
  const days = result?.timeline ?? []
  const structure: PhaseLabStructure | undefined = structureQuery.data

  // K 线数据源按视图切换：走势结构用 /structure 的 bars，形态信号用 timeline
  const bars = useMemo<OHLC[]>(() => {
    if (mode === 'structure') {
      return (structure?.bars ?? []).map(b => ({
        date: b.date,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: b.volume,
      }))
    }
    return days
      .filter(d => d.ohlcv.open != null && d.ohlcv.high != null && d.ohlcv.low != null && d.ohlcv.close != null)
      .map(d => ({
        date: d.date,
        open: d.ohlcv.open as number,
        high: d.ohlcv.high as number,
        low: d.ohlcv.low as number,
        close: d.ohlcv.close as number,
        volume: d.ohlcv.volume ?? 0,
      }))
  }, [mode, structure, days])

  // ── signals 视图：每个命中日一个聚合点位；悬浮阶段 / 点击事件时打 emphasis / muted ──
  const markers = useMemo<ChartMarker[]>(() => days
    .filter(d => d.fired.length > 0)
    .map(d => {
      const key = toneKeyOf(d.phase)
      const hoverMatch = hoverTone != null && key === hoverTone
      const clicked = hoverTone == null && selectedDate === d.date
      return {
        date: d.date,
        kind: 'neutral' as const,
        above: true,
        label: shortCodes(d),
        color: TONES[key].color,
        ...(hoverMatch || clicked ? { emphasis: true } : {}),
        ...(hoverTone != null && !hoverMatch ? { muted: true } : {}),
      }
    }), [days, hoverTone, selectedDate])

  const toneCounts = useMemo(() => {
    const counts: Record<ToneKey, number> = { accum: 0, shakeout: 0, markup: 0, distribution: 0, neutral: 0 }
    for (const d of days) if (d.fired.length > 0) counts[toneKeyOf(d.phase)] += 1
    return counts
  }, [days])

  const events = useMemo(() => days.filter(d => d.fired.length > 0), [days])

  // ── structure 视图：趋势段 markArea + 标准答案变点（黄色） ──
  const structureRanges = useMemo<ChartRange[]>(() => {
    if (!structure) return []
    return structure.ground_truth.segments.map(s => ({
      start: structure.dates[s.start] ?? '',
      end: structure.dates[s.end] ?? '',
      label: `${DIR[s.direction].label} ${s.change_pct > 0 ? '+' : ''}${s.change_pct.toFixed(1)}%`,
      color: DIR[s.direction].area,
    })).filter(r => r.start && r.end)
  }, [structure])

  const structureMarkers = useMemo<ChartMarker[]>(() => {
    if (!structure) return []
    return structure.ground_truth.bkps
      .filter(i => structure.dates[i])
      .map((i, k) => ({
        date: structure.dates[i],
        kind: 'neutral' as const,
        above: true,
        label: `CP${k + 1}`,
        color: '#FACC15',
        emphasis: true,
      }))
  }, [structure])

  // 点击 K 线 / 色带 → 时间轴列表滚到对应事件（signals 视图）
  useEffect(() => {
    if (!selectedDate || !listRef.current) return
    const el = listRef.current.querySelector(`[data-date="${selectedDate}"]`)
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selectedDate])

  const headerRight = mode === 'structure'
    ? (structure
        ? `${structure.symbol} · 近 ${structure.lookback} 交易日 · ${structure.from_date} ~ ${structure.to_date}`
        : '离线结构标注')
    : (result
        ? `${result.symbol} · 近 ${result.lookback} 交易日 · ${result.from_date} ~ ${result.to_date}`
        : '形态信号（旧视图）')

  const activeLoading = mode === 'structure' ? structureQuery.isLoading : query.isLoading
  const activeError = mode === 'structure' ? structureQuery.isError : query.isError
  const activeErrorMsg = (mode === 'structure' ? structureQuery.error : query.error) as Error | null
  const activeRefetch = mode === 'structure' ? structureQuery.refetch : query.refetch
  const activeRefetching = mode === 'structure' ? structureQuery.isRefetching : query.isRefetching

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="价格结构识别"
        subtitle="用展示区间的全量日线（含未来数据）做离线最优分割，得到客观走势结构；仅作离线结构标注，不可用于实盘"
        right={
          <span className="shrink-0 rounded-md border border-border px-2 py-1 text-[11px] text-muted">
            {headerRight}
          </span>
        }
      />

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {/* 控制条 */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <div className="flex h-9 items-center gap-2 rounded-md border border-border bg-surface px-2">
              <Search className="h-4 w-4 shrink-0 text-muted" />
              <input
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') submit() }}
                placeholder="股票代码，如 000006.SZ"
                className="w-52 bg-transparent text-sm text-foreground outline-none placeholder:text-muted"
                aria-label="股票代码"
              />
            </div>
            {suggestions.length > 0 && (
              <div className="absolute left-0 top-10 z-20 w-64 overflow-hidden rounded-md border border-border bg-surface shadow-lg">
                {suggestions.map(s => (
                  <button
                    key={s.symbol}
                    type="button"
                    className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs hover:bg-elevated"
                    onClick={() => { setInput(s.symbol); setSymbol(s.symbol); setSuggestions([]); resetSelection() }}
                  >
                    <span className="text-foreground">{s.symbol}</span>
                    <span className="truncate text-muted">{s.name}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={submit}
            className="h-9 rounded-md bg-accent px-3 text-sm text-white hover:opacity-90"
          >
            查看
          </button>

          {/* 视图切换 */}
          <div className="ml-1 flex items-center gap-1 rounded-md border border-border bg-surface p-0.5">
            {([['structure', '走势结构'], ['signals', '形态信号（旧视图）']] as const).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => { setMode(key); resetSelection() }}
                className={`rounded px-2 py-1 text-xs ${mode === key ? 'bg-elevated text-foreground' : 'text-muted hover:text-foreground'}`}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1 rounded-md border border-border bg-surface p-0.5">
            {LOOKBACKS.map(n => (
              <button
                key={n}
                type="button"
                onClick={() => { setLookback(n); resetSelection() }}
                className={`rounded px-2 py-1 text-xs ${lookback === n ? 'bg-elevated text-foreground' : 'text-muted hover:text-foreground'}`}
              >
                {n} 日
              </button>
            ))}
          </div>

          {mode === 'structure' ? (
            <div className="flex items-center gap-1 rounded-md border border-border bg-surface p-0.5">
              <span className="px-1.5 text-[11px] text-muted">pen</span>
              {(structure?.pen_options ?? [0.02, 0.05, 0.1, 0.3]).map(p => (
                <button
                  key={p}
                  type="button"
                  title="惩罚越大，分段越粗"
                  onClick={() => setPen(p)}
                  className={`rounded px-2 py-1 text-xs ${pen === p ? 'bg-elevated text-foreground' : 'text-muted hover:text-foreground'}`}
                >
                  {p}
                </button>
              ))}
            </div>
          ) : (
            <div className="flex items-center gap-1 rounded-md border border-border bg-surface p-0.5">
              {(['all', 'effective'] as const).map(m => (
                <button
                  key={m}
                  type="button"
                  onClick={() => { setRules(m); resetSelection() }}
                  className={`rounded px-2 py-1 text-xs ${rules === m ? 'bg-elevated text-foreground' : 'text-muted hover:text-foreground'}`}
                >
                  {m === 'all' ? '全部 26 条' : '核心有效 19 条'}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* 常驻说明条：按视图切换口径 */}
        <div className="mt-3 flex items-start gap-2 rounded-md border border-border bg-elevated/40 px-3 py-2 text-[11px] leading-relaxed text-secondary">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" />
          {mode === 'structure' ? (
            <div className="space-y-0.5">
              <div><b className="font-medium text-foreground">标准答案</b>＝对展示区间<b className="font-medium text-foreground">全量</b>日线跑一次离线最优分割（ruptures Pelt / l2），<b className="font-medium text-foreground">明确用到了未来数据，仅作离线结构标注，不可用于实盘</b>。它是「给定算法 + pen 下的最优分段」，不是绝对真理；pen 越大分段越粗。</div>
              <div>段方向按每段首尾收盘价涨跌 ±5% 分为上涨 / 下跌 / 横盘，只用于着色；<b className="font-medium text-foreground">不检验任何实时算法、不产出准确率评分</b>。右侧「分段明细」列出每段起止日期、交易日数、首尾收盘价与涨跌幅，供肉眼核对变点位置是否贴合真实走势。</div>
              {structure?.elapsed_ms != null && <div>本次离线分割耗时 {structure.elapsed_ms} ms（结果缓存 60s）。</div>}
            </div>
          ) : (
            <div className="space-y-0.5">
              <div>形态由 vendored hot_money_radar 的 <b className="font-medium text-foreground">{result?.rule_count ?? 26} 条规则</b>逐日 PIT 重放（每日只用当日及以前的日线）；<b className="font-medium text-foreground">不构成实盘信号</b>。</div>
              <div>注意：这里的「主导阶段」是<b className="font-medium text-foreground">选股信号命中日的汇总标签</b>，不是价格结构识别；要看用未来数据算出的客观走势结构，请切回「走势结构」视图。</div>
              <div>规则是<b className="font-medium text-foreground">稀疏事件型</b>：只在「首次确认 / 突破 / 破位」那一刻触发，大片空白是正常结果，不是漏算。</div>
              {result?.chip_degraded && (
                <div className="text-foreground/80">
                  本地无换手率数据：<b className="font-medium">{result.degraded_rules.join(' / ')}</b> 依赖筹码/换手率的判定按上游兜底逻辑退化（缺数据反而更易误报），结果可能不完整；此处不伪造、不估算缺失值。
                </div>
              )}
              <div>时间轴里的「上游复测结论」来自参考仓库在其自有双池上的结论，<b className="font-medium text-foreground">未经本系统复测</b>。</div>
            </div>
          )}
        </div>

        {/* 图例 */}
        {mode === 'structure' ? (
          <div className="mt-3 flex flex-wrap items-center gap-3 text-[11px] text-muted">
            <span>方向色标（客观涨跌：红涨 / 绿跌 / 灰横盘）：</span>
            {(Object.keys(DIR) as (keyof typeof DIR)[]).map(k => (
              <span key={k} className="inline-flex items-center gap-1">
                <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: DIR[k].band }} />
                {DIR[k].label}
              </span>
            ))}
            <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: '#FACC15' }} />标准答案变点</span>
            {structure && <span className="ml-auto"><b className="text-foreground">{structure.ground_truth.bkps.length}</b> 个变点 · <b className="text-foreground">{structure.ground_truth.segment_count}</b> 段</span>}
          </div>
        ) : (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-muted">
            <span>阶段色标（中性色，非涨跌语义，悬浮高亮）：</span>
            {(Object.keys(TONES) as ToneKey[]).map(key => {
              const t = TONES[key]
              const active = hoverTone === key
              return (
                <button
                  key={key}
                  type="button"
                  onMouseEnter={() => setHoverTone(key)}
                  onMouseLeave={() => setHoverTone(null)}
                  onFocus={() => setHoverTone(key)}
                  onBlur={() => setHoverTone(null)}
                  title={`悬浮高亮「${t.label}」的 ${toneCounts[key]} 个命中点位`}
                  className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 transition-colors ${active ? 'bg-elevated text-foreground' : 'hover:bg-elevated/60'}`}
                >
                  <span
                    className="inline-block h-2.5 w-2.5 rounded-sm"
                    style={{ background: t.color, outline: active ? '2px solid #FACC15' : undefined, outlineOffset: 1 }}
                  />
                  {t.label}
                  <span className="font-mono text-foreground/70">{toneCounts[key]}</span>
                </button>
              )
            })}
            {result && <span className="ml-auto">命中 <b className="text-foreground">{result.hit_days}</b> / {result.total_days} 天</span>}
          </div>
        )}

        {/* 主体 */}
        {activeLoading ? (
          <div className="mt-3 grid h-[480px] place-items-center rounded-md border border-border bg-surface text-sm text-muted">
            {mode === 'structure' ? '正在计算走势结构…' : '正在读取日线与重放形态…'}
          </div>
        ) : activeError ? (
          <div className="mt-3 h-[480px] rounded-md border border-border bg-surface">
            <ErrorState hint={activeErrorMsg?.message || '走势结构计算失败'} onRetry={() => activeRefetch()} retrying={activeRefetching} />
          </div>
        ) : bars.length === 0 ? (
          <div className="mt-3 h-[480px] rounded-md border border-border bg-surface">
            <EmptyState title="没有可展示的日线" hint="试试换个股票代码，或确认本地 enriched 数据里有该标的。" />
          </div>
        ) : (
          <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_380px]">
            <div className="min-w-0 space-y-2">
              <div className="rounded-md border border-border bg-surface p-1">
                <EChartsCandlestick
                  key={`${mode === 'structure' ? structure?.symbol : result?.symbol}|${lookback}|${mode}|${mode === 'structure' ? `pen${pen}` : rules}`}
                  data={bars}
                  markers={mode === 'structure' ? structureMarkers : markers}
                  ranges={mode === 'structure' ? structureRanges : undefined}
                  height={480}
                  visibleBars="all"
                  activeIndicators={['vol']}
                  onDateClick={mode === 'signals' ? date => setSelectedDate(date) : undefined}
                  symbol={mode === 'structure' ? structure?.symbol : result?.symbol}
                />
              </div>

              {mode === 'structure' ? (
                <>
                  {/* 分段色带：每段一个色块，段边界竖线 */}
                  <div className="flex h-3 w-full overflow-hidden rounded-sm border border-border bg-elevated/30">
                    {(structure?.ground_truth.segments ?? []).map((s, i) => (
                      <div
                        key={`${s.start}-${i}`}
                        title={`${DIR[s.direction].label} ${s.from} ~ ${s.to} · ${s.change_pct > 0 ? '+' : ''}${s.change_pct.toFixed(2)}%`}
                        className="h-full min-w-0"
                        style={{
                          width: `${((s.end - s.start + 1) / Math.max(1, structure?.n_obs ?? 1)) * 100}%`,
                          background: DIR[s.direction].band,
                          borderLeft: i > 0 ? '1px solid rgba(15,23,42,0.55)' : undefined,
                        }}
                      />
                    ))}
                  </div>
                  <div className="text-[10px] text-muted">
                    上方色带为离线最优分割出的趋势段（段边界＝标准答案变点）；K 线底色同步标注。<b className="font-medium text-foreground">黄点＝标准答案变点</b>，右侧「分段明细」可逐段核对起止、交易日数与首尾收盘价。
                  </div>
                  {structureQuery.isLoading && <div className="text-[11px] text-muted">正在更新走势结构…</div>}
                </>
              ) : (
                <>
                  {/* 阶段色带：与 K 线同一日期序列，命中日按主导阶段着色 */}
                  <div className="flex h-3 w-full gap-px overflow-hidden rounded-sm border border-border bg-elevated/30">
                    {days.map(d => {
                      const hit = d.fired.length > 0
                      const key = toneKeyOf(d.phase)
                      const tone = hit ? TONES[key] : null
                      const hoverMatch = hoverTone != null && hit && key === hoverTone
                      const dimmed = hoverTone != null && !hoverMatch
                      const selected = selectedDate === d.date
                      return (
                        <button
                          key={d.date}
                          type="button"
                          title={`${d.date} · ${d.phase}${hit ? ` · ${d.fired.map(f => `${f.code} ${f.name}`).join('；')}` : ' · 无形态命中'}`}
                          onClick={() => setSelectedDate(d.date)}
                          className="min-w-0 flex-1 border-0 p-0"
                          style={{
                            background: tone ? tone.color : 'transparent',
                            opacity: dimmed ? 0.25 : 1,
                            boxShadow: hoverMatch ? 'inset 0 0 0 1px rgba(255,255,255,0.9)' : undefined,
                            outline: selected ? '2px solid #FACC15' : undefined,
                            outlineOffset: -1,
                          }}
                          aria-label={`${d.date} ${d.phase}`}
                        />
                      )
                    })}
                  </div>
                  <div className="text-[10px] text-muted">
                    悬浮上方阶段色标可高亮该阶段全部命中点位（其余淡化）；点击时间轴事件、K 线或色带可高亮并定位对应点位；留白表示当日无形态命中。
                  </div>
                </>
              )}
            </div>

            {/* 右列：分段明细 / 操作步骤时间轴 */}
            {mode === 'structure' ? (
              structure ? <SegmentTable data={structure} /> : (
                <div className="grid min-h-0 place-items-center rounded-md border border-border bg-surface text-[11px] text-muted">正在计算…</div>
              )
            ) : (
              <div className="flex min-w-0 flex-col rounded-md border border-border bg-surface">
                <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs">
                  <Activity className="h-3.5 w-3.5 text-muted" />
                  <span className="font-medium text-foreground">操作步骤时间轴</span>
                  <span className="text-muted">命中 {events.length} 天</span>
                </div>
                <div ref={listRef} className="max-h-[524px] min-h-0 overflow-auto">
                  {events.length === 0 ? (
                    <EmptyState title="该区间没有形态命中" hint="换个周期或股票试试；稀疏命中是上游规则的正常特性。" />
                  ) : events.map(day => {
                    const tone = toneOf(day.phase)
                    const active = selectedDate === day.date
                    return (
                      <div
                        key={day.date}
                        data-date={day.date}
                        onClick={() => setSelectedDate(day.date)}
                        className={`cursor-pointer border-b border-border/60 px-3 py-2.5 text-[11px] leading-relaxed ${active ? 'bg-elevated' : 'hover:bg-elevated/50'}`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-foreground">{day.date}</span>
                          <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px]" style={{ background: `${tone.color}22`, color: tone.color }}>
                            {day.phase}
                          </span>
                        </div>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {day.fired.map(f => (
                            <span key={f.code} className="rounded border border-border px-1 py-0.5 font-mono text-[10px] text-secondary" title={f.name}>
                              {f.code} {f.name}
                            </span>
                          ))}
                        </div>
                        {active && (
                          <div className="mt-2 space-y-1.5 border-l-2 pl-2" style={{ borderColor: tone.color }}>
                            {day.fired.map(f => (
                              <div key={f.code} className="space-y-0.5">
                                <div className="font-mono text-[10px] text-foreground">{f.code} · 触发条件</div>
                                <div className="text-secondary">{f.reason}</div>
                                <div className="text-[10px] text-secondary/80">上游复测结论（未经本系统复测）：{f.retest}</div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
