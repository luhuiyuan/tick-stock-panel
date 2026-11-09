import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Flame, Info, Search } from 'lucide-react'
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import type { OHLC } from '@/components/EChartsCandlestick'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { PageHeader } from '@/components/PageHeader'
import { api } from '@/lib/api'
import type { StockEmotion } from '@/lib/api'

const LOOKBACKS = [60, 120, 250, 500] as const

/** 情绪分 → 颜色（恐惧=绿、亢奋=红，遵循 A 股红涨绿跌语义）。 */
const TONE_COLOR: Record<string, string> = {
  极度亢奋: '#F87171',
  亢奋: '#FCA5A5',
  中性: '#94A3B8',
  恐惧: '#86EFAC',
  极度恐惧: '#4ADE80',
}
function toneColor(label: string): string {
  return TONE_COLOR[label] ?? 'transparent'
}

/** 情绪分曲线（独立 echarts 折线，含档位阈值线）。 */
function EmotionScoreChart({ data }: { data: StockEmotion }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current)
    const option: EChartsOption = {
      grid: { left: 34, right: 12, top: 14, bottom: 20 },
      tooltip: { trigger: 'axis' },
      xAxis: {
        type: 'category',
        data: data.dates,
        axisLabel: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: 100,
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.15)' } },
      },
      series: [
        {
          name: '情绪分',
          type: 'line',
          data: data.scores,
          connectNulls: false,
          showSymbol: false,
          lineStyle: { width: 1.5 },
          areaStyle: { opacity: 0.06 },
          markLine: {
            silent: true,
            symbol: 'none',
            data: [
              { yAxis: data.thresholds.euphoria, lineStyle: { color: '#F87171' }, label: { formatter: '极度亢奋' } },
              { yAxis: data.thresholds.excited, lineStyle: { color: '#FCA5A5' }, label: { formatter: '亢奋' } },
              { yAxis: data.thresholds.neutral, lineStyle: { color: '#94A3B8' }, label: { formatter: '中性' } },
              { yAxis: data.thresholds.fear, lineStyle: { color: '#86EFAC' }, label: { formatter: '恐惧' } },
            ],
          },
        },
      ],
    }
    chart.setOption(option)
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [data])
  return <div ref={ref} style={{ width: '100%', height: 200 }} />
}

/** 右下：近 30 个有效交易日的情绪分明细。 */
function ScoreTable({ data }: { data: StockEmotion }) {
  const rows = useMemo(() => {
    const out: { date: string; score: number; label: string }[] = []
    for (let i = 0; i < data.dates.length; i++) {
      const s = data.scores[i]
      if (s == null) continue
      out.push({ date: data.dates[i], score: s, label: data.labels[i] })
    }
    return out.slice(-30).reverse()
  }, [data])
  return (
    <div className="flex min-w-0 flex-col rounded-md border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs">
        <Flame className="h-3.5 w-3.5 text-muted" />
        <span className="font-medium text-foreground">情绪分明细</span>
        <span className="ml-auto text-muted">近 {rows.length} 个有效交易日</span>
      </div>
      <div className="max-h-[420px] overflow-auto">
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 bg-elevated/95 text-muted backdrop-blur">
            <tr>
              <th className="px-2 py-1.5 text-left font-normal">日期</th>
              <th className="px-2 py-1.5 text-right font-normal">情绪分</th>
              <th className="px-2 py-1.5 text-left font-normal">档位</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.date} className="border-t border-border/60">
                <td className="px-2 py-1.5 font-mono text-muted">{r.date}</td>
                <td className="px-2 py-1.5 text-right font-mono text-foreground">{r.score}</td>
                <td className="px-2 py-1.5">
                  <span
                    className="inline-block h-2.5 w-2.5 rounded-sm align-middle"
                    style={{ background: toneColor(r.label), marginRight: 4 }}
                  />
                  <span style={{ color: toneColor(r.label) }}>{r.label}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function StockEmotionPage() {
  const [input, setInput] = useState('000006.SZ')
  const [symbol, setSymbol] = useState('000006.SZ')
  const [lookback, setLookback] = useState<number>(250)

  const submit = () => {
    const v = input.trim().toUpperCase()
    if (v) setSymbol(v)
  }

  const query = useQuery({
    queryKey: ['stock-emotion', symbol, lookback],
    queryFn: () => api.stockEmotion({ symbol, lookback }),
    enabled: symbol.trim().length > 0,
  })
  const data = query.data
  const loading = query.isLoading
  const error = query.error as { message?: string } | null
  const bars: OHLC[] = useMemo(
    () =>
      (data?.bars ?? []).map(b => ({
        date: b.date,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: b.volume,
      })),
    [data],
  )

  const headerRight = data
    ? `当前 ${data.current.date} · 分 ${data.current.score ?? '—'} · ${data.current.label}`
    : '个股情绪'

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        title="个股情绪"
        subtitle="用个股近期价格走势给单只股票打「恐惧↔亢奋」连续分：大跌→恐惧、大涨→亢奋。属于对当前冷热的标注，不是涨跌预测"
        right={
          <span className="shrink-0 rounded-md border border-border px-2 py-1 text-[11px] text-muted">
            {headerRight}
          </span>
        }
      />

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {/* 控制条 */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex h-9 items-center gap-2 rounded-md border border-border bg-surface px-2">
            <Search className="h-4 w-4 shrink-0 text-muted" />
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') submit()
              }}
              placeholder="股票代码，如 000006.SZ"
              className="w-52 bg-transparent text-sm text-foreground outline-none placeholder:text-muted"
              aria-label="股票代码"
            />
          </div>
          <div className="flex items-center gap-1 text-[11px] text-muted">
            <span className="px-1">周期</span>
            {LOOKBACKS.map(n => (
              <button
                key={n}
                type="button"
                onClick={() => setLookback(n)}
                className={`rounded px-2 py-1 text-xs ${lookback === n ? 'bg-elevated text-foreground' : 'text-muted hover:text-foreground'}`}
              >
                {n}日
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <div className="mt-3 grid h-[480px] place-items-center rounded-md border border-border bg-surface text-sm text-muted">
            正在读取日线并计算情绪分…
          </div>
        ) : error ? (
          <div className="mt-3 h-[480px] rounded-md border border-border bg-surface">
            <ErrorState hint={error?.message || '情绪分计算失败'} onRetry={() => query.refetch()} retrying={query.isFetching} />
          </div>
        ) : !data || bars.length === 0 ? (
          <div className="mt-3 h-[480px] rounded-md border border-border bg-surface">
            <EmptyState title="没有可展示的日线" hint="试试换个股票代码，或确认本地 enriched 数据里有该标的。" />
          </div>
        ) : (
          <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_380px]">
            <div className="min-w-0 space-y-2">
              <div className="rounded-md border border-border bg-surface p-1">
                <EChartsCandlestick
                  key={`${data.symbol}|${lookback}`}
                  data={bars}
                  height={480}
                  visibleBars="all"
                  activeIndicators={['vol']}
                  symbol={data.symbol}
                />
              </div>

              {/* 情绪色带：与 K 线同一日期序列，按当日档位着色 */}
              <div className="flex h-3 w-full gap-px overflow-hidden rounded-sm border border-border bg-elevated/30">
                {data.dates.map((d, i) => (
                  <div
                    key={d}
                    title={`${d} · ${data.labels[i]} · 分 ${data.scores[i] ?? '—'}`}
                    className="min-w-0 flex-1"
                    style={{ background: toneColor(data.labels[i]) }}
                    aria-label={`${d} ${data.labels[i]}`}
                  />
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
                <span>档位色：</span>
                {Object.entries(TONE_COLOR).map(([label, color]) => (
                  <span key={label} className="inline-flex items-center gap-1">
                    <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
                    {label}
                  </span>
                ))}
              </div>

              {/* 情绪分曲线 */}
              <div className="rounded-md border border-border bg-surface p-1">
                <div className="px-1 pt-1 text-[11px] text-muted">情绪分曲线（0=极度恐惧 · 100=极度亢奋）</div>
                <EmotionScoreChart data={data} />
              </div>
            </div>

            <ScoreTable data={data} />
          </div>
        )}

        {/* 口径说明 */}
        <div className="mt-3">
          <details className="rounded-md border border-border px-2 py-1.5 text-[10px] leading-relaxed text-muted" open>
            <summary className="flex cursor-pointer items-center gap-1 font-medium text-foreground/80">
              <Info className="h-3.5 w-3.5" />
              口径与说明
            </summary>
            <ul className="mt-1.5 list-disc space-y-1 pl-4">
              {(data?.notes ?? []).map((note, i) => (
                <li key={i}>{note}</li>
              ))}
            </ul>
          </details>
        </div>
      </div>
    </div>
  )
}
