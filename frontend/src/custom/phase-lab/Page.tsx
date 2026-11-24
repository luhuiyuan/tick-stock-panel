import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, Info, Search } from 'lucide-react'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import type { ChartMarker, OHLC } from '@/components/EChartsCandlestick'
import type { ChartStructures } from '@/components/chartStructures'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { PageHeader } from '@/components/PageHeader'
import { api } from '@/lib/api'
import type {
  PhaseLabDay,
  PhaseLabTimeline,
  PhaseLabWaves,
} from '@/lib/api'

const LOOKBACKS = [60, 250, 500, 1000] as const
type Lookback = typeof LOOKBACKS[number]
type RuleMode = 'all' | 'effective'
type ViewMode = 'waves' | 'signals'
type WaveLevel = 'L0' | 'L1' | 'L2'

const WAVE_COLORS: Record<WaveLevel, string> = { L0: '#A78BFA', L1: '#38BDF8', L2: '#F59E0B' }

/**
 * 「主力阶段」配色刻意用中性色（蓝/紫/橙/灰），不与 A 股「红涨绿跌」混淆。
 * 阶段字符串来自上游 _pattern_phase，含 emoji 后缀，按关键字归类。
 */
const TONES = {
  accum: { color: '#3B82F6', label: '吸筹' },
  shakeout: { color: '#8B5CF6', label: '试盘 / 洗盘' },
  markup: { color: '#F97316', label: '突破 / 拉升' },
  distribution: { color: '#94A3B8', label: '出货 / 风控' },
  neutral: { color: '#6B7280', label: '观望' },
} as const
type ToneKey = keyof typeof TONES

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

export function PhaseLabPage() {
  const [input, setInput] = useState('000006.SZ')
  const [symbol, setSymbol] = useState('000006.SZ')
  const [lookback, setLookback] = useState<Lookback>(250)
  const [rules, setRules] = useState<RuleMode>('all')
  const [mode, setMode] = useState<ViewMode>('waves')
  const [activeLevels, setActiveLevels] = useState<Set<WaveLevel>>(() => new Set(['L1']))
  const [suggestions, setSuggestions] = useState<{ symbol: string; name: string }[]>([])
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  // 悬浮阶段色标时的预览高亮，优先级高于点击选中；移出即恢复。
  const [hoverTone, setHoverTone] = useState<ToneKey | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const query = useQuery({
    queryKey: ['phase-lab', symbol, lookback, rules],
    queryFn: () => api.phaseLabTimeline({ symbol, lookback, rules }),
    enabled: !!symbol && mode === 'signals',
    staleTime: 30_000,
  })

  const wavesQuery = useQuery({
    queryKey: ['phase-lab-waves', symbol, lookback],
    queryFn: () => api.phaseLabWaves({ symbol, lookback }),
    enabled: !!symbol && mode === 'waves',
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
  const waveResult: PhaseLabWaves | undefined = wavesQuery.data
  const days = result?.timeline ?? []

  const signalBars = useMemo<OHLC[]>(() => {
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
  }, [days])

  const waveBars = useMemo<OHLC[]>(() => (waveResult?.bars ?? []).map(bar => ({ ...bar })), [waveResult])
  const bars = mode === 'waves' ? waveBars : signalBars

  const structures = useMemo<ChartStructures>(() => {
    if (!waveResult) return {}
    const segments: NonNullable<ChartStructures['segments']> = []
    for (const level of ['L0', 'L1', 'L2'] as const) {
      if (!activeLevels.has(level)) continue
      const wave = waveResult.wave_structure[level]
      for (const segment of [...wave.segments, ...(wave.current_tail ? [wave.current_tail] : [])]) {
        segments.push({
          start: segment.start_date, end: segment.end_date,
          start_price: segment.start_price, end_price: segment.end_price,
          color: WAVE_COLORS[level], width: level === 'L1' ? 2.5 : level === 'L2' ? 2 : 1.5,
          dashed: segment.confirmed_date == null,
        })
      }
    }
    return { segments }
  }, [waveResult, activeLevels])

  // 每个命中日一个聚合点位；悬浮阶段或点击事件时高亮。
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

  // 点击 K 线 / 色带 → 时间轴列表滚到对应事件
  useEffect(() => {
    if (!selectedDate || !listRef.current) return
    const el = listRef.current.querySelector(`[data-date="${selectedDate}"]`)
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selectedDate])

  const headerRight = mode === 'waves'
    ? (waveResult ? `${waveResult.symbol} · 近 ${waveResult.lookback} 交易日 · ${waveResult.from_date ?? '—'} ~ ${waveResult.to_date ?? '—'}` : '历史分级波段')
    : (result ? `${result.symbol} · 近 ${result.lookback} 交易日 · ${result.from_date} ~ ${result.to_date}` : '形态信号')

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="历史走势研究"
        subtitle="查看历史分级波段结构，或切换到形态信号时间线"
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

          <div className="flex items-center gap-1 rounded-md border border-border bg-surface p-0.5">
            {([['waves', '历史波段'], ['signals', '形态信号']] as const).map(([key, label]) => (
              <button key={key} type="button" onClick={() => { setMode(key); resetSelection() }}
                className={`rounded px-2 py-1 text-xs ${mode === key ? 'bg-elevated text-foreground' : 'text-muted hover:text-foreground'}`}>
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

          {mode === 'signals' ? (
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
          ) : (
            <div className="flex items-center gap-1 rounded-md border border-border bg-surface p-0.5">
              {(['L0', 'L1', 'L2'] as const).map(level => (
                <button key={level} type="button" aria-pressed={activeLevels.has(level)}
                  onClick={() => setActiveLevels(previous => { const next = new Set(previous); if (next.has(level)) next.delete(level); else next.add(level); return next })}
                  className={`rounded px-2 py-1 text-xs ${activeLevels.has(level) ? 'bg-elevated text-foreground' : 'text-muted hover:text-foreground'}`}
                  style={activeLevels.has(level) ? { color: WAVE_COLORS[level] } : undefined}
                  title={`${level}：${waveResult?.wave_structure[level].confirmation_bars ?? ({ L0: 3, L1: 7, L2: 15 } as const)[level]} 根 K 线确认；虚线为当前未完成尾段`}>
                  {level}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="mt-3 flex items-start gap-2 rounded-md border border-border bg-elevated/40 px-3 py-2 text-[11px] leading-relaxed text-secondary">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted" />
          {mode === 'waves' ? (
            <div className="space-y-0.5">
              <div>L0/L1/L2 使用 <b className="font-medium text-foreground">3/7/15 根 K 线</b>确认局部高低点并连接为波段；实线是已闭合波段，虚线是当前尾段。</div>
              <div><b className="font-medium text-foreground">这是历史结构展示，不是实时预测或交易信号。</b>局部高低点只有经过对应确认窗口后才会显示。</div>
              {waveResult?.elapsed_ms != null && <div>计算耗时 {waveResult.elapsed_ms} ms。</div>}
            </div>
          ) : (
            <div className="space-y-0.5">
              <div>形态由 vendored hot_money_radar 的 <b className="font-medium text-foreground">{result?.rule_count ?? 26} 条规则</b>逐日 PIT 重放（每日只用当日及以前的日线）；<b className="font-medium text-foreground">不构成实盘信号</b>。</div>
              <div>「主导阶段」是选股信号命中日的汇总标签，不是价格结构识别。</div>
              <div>规则是稀疏事件型：只在首次确认、突破或破位时触发，大片空白是正常结果，不是漏算。</div>
              {result?.chip_degraded && <div>本地无换手率数据：<b className="font-medium">{result.degraded_rules.join(' / ')}</b> 相关规则退化，结果可能不完整；此处不估算缺失值。</div>}
              <div>「上游复测结论」未经本系统复测，仅供参考。</div>
            </div>
          )}
        </div>

        {mode === 'waves' ? (
          <div className="mt-3 flex flex-wrap items-center gap-3 text-[11px] text-muted">
            <span>分级波段：</span>
            {(['L0', 'L1', 'L2'] as const).map(level => (
              <span key={level} className="inline-flex items-center gap-1" style={{ color: WAVE_COLORS[level] }}>
                <span className="inline-block h-0.5 w-4" style={{ background: WAVE_COLORS[level] }} />{level}
                {waveResult && <span className="font-mono text-muted">{waveResult.wave_structure[level].segment_count} 段</span>}
              </span>
            ))}
            <span className="ml-auto">实线：已确认 · 虚线：当前尾段</span>
          </div>
        ) : (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-muted">
            <span>阶段色标（中性色，非涨跌语义，悬浮高亮）：</span>
            {(Object.keys(TONES) as ToneKey[]).map(key => {
              const t = TONES[key]
              const active = hoverTone === key
              return (
                <button key={key} type="button" onMouseEnter={() => setHoverTone(key)} onMouseLeave={() => setHoverTone(null)}
                  onFocus={() => setHoverTone(key)} onBlur={() => setHoverTone(null)}
                  title={`悬浮高亮「${t.label}」的 ${toneCounts[key]} 个命中点位`}
                  className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 transition-colors ${active ? 'bg-elevated text-foreground' : 'hover:bg-elevated/60'}`}>
                  <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: t.color, outline: active ? '2px solid #FACC15' : undefined, outlineOffset: 1 }} />
                  {t.label}<span className="font-mono text-foreground/70">{toneCounts[key]}</span>
                </button>
              )
            })}
            {result && <span className="ml-auto">命中 <b className="text-foreground">{result.hit_days}</b> / {result.total_days} 天</span>}
          </div>
        )}

        {/* 主体 */}
        {(mode === 'waves' ? wavesQuery.isLoading : query.isLoading) ? (
          <div className="mt-3 grid h-[480px] place-items-center rounded-md border border-border bg-surface text-sm text-muted">
            {mode === 'waves' ? '正在计算历史分级波段…' : '正在读取日线与重放形态…'}
          </div>
        ) : (mode === 'waves' ? wavesQuery.isError : query.isError) ? (
          <div className="mt-3 h-[480px] rounded-md border border-border bg-surface">
            <ErrorState hint={(mode === 'waves' ? wavesQuery.error?.message : query.error?.message) || (mode === 'waves' ? '历史波段加载失败' : '形态信号加载失败')} onRetry={() => mode === 'waves' ? wavesQuery.refetch() : query.refetch()} retrying={mode === 'waves' ? wavesQuery.isRefetching : query.isRefetching} />
          </div>
        ) : bars.length === 0 ? (
          <div className="mt-3 h-[480px] rounded-md border border-border bg-surface">
            <EmptyState title="没有可展示的日线" hint="试试换个股票代码，或确认本地 enriched 数据里有该标的。" />
          </div>
        ) : (
          <div className={`mt-3 grid gap-3 ${mode === 'signals' ? 'lg:grid-cols-[minmax(0,1fr)_380px]' : 'grid-cols-1'}`}>
            <div className="min-w-0 space-y-2">
              <div className="rounded-md border border-border bg-surface p-1">
                <EChartsCandlestick
                  key={`${mode === 'waves' ? waveResult?.symbol : result?.symbol}|${lookback}|${mode}|${rules}`}
                  data={bars}
                  markers={mode === 'waves' ? undefined : markers}
                  structures={mode === 'waves' ? structures : undefined}
                  height={480}
                  visibleBars="all"
                  activeIndicators={['vol']}
                  onDateClick={setSelectedDate}
                  symbol={mode === 'waves' ? waveResult?.symbol : result?.symbol}
                />
              </div>

              {mode === 'signals' && <>
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
              </>}
            </div>

            {mode === 'signals' && <div className="flex min-w-0 flex-col rounded-md border border-border bg-surface">
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
            </div>}
          </div>
        )}
      </div>
    </div>
  )
}
