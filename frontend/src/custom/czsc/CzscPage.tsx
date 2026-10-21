import { useMemo, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { api, type ChanAssetType, type ChanTimeframe } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cnSignal, STRUCTURE_COVERAGE_REASON_LABELS, CHAN_BSP_LABELS } from '@/lib/signals'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import type { ChartStructures } from '@/components/chartStructures'

const LAYERS = { fractals: '分型', strokes: '笔', centers: '笔中枢', segments: '线段', segmentCenters: '线段中枢', candidates: '买卖点端点（含候选）', unfinished: '未完成结构', signals: '确认事件' }
const PERIODS: Record<ChanTimeframe, string> = { '1m': '1分', '5m': '5分', '30m': '30分', '1d': '日线', '1w': '周线' }
const INDICATORS = ['vol', 'macd']
const VOLUME_COMPARE = { enabled: false, days: 1 }

export default function CzscPage() {
  const [params, setParams] = useSearchParams()
  const symbol = params.get('symbol') ?? ''
  const requestedAsset = params.get('asset_type')
  const assetType: ChanAssetType | undefined = requestedAsset === 'stock' || requestedAsset === 'index' ? requestedAsset : undefined
  const validAsset = requestedAsset === null || assetType !== undefined
  const requestedPeriod = params.get('timeframe') ?? '1d'
  const validPeriod = Object.hasOwn(PERIODS, requestedPeriod)
  const timeframe: ChanTimeframe = validPeriod ? requestedPeriod as ChanTimeframe : '1d'
  const periodLabel = PERIODS[timeframe]
  const minute = timeframe.endsWith('m')
  const [layers, setLayers] = useState<Record<keyof typeof LAYERS, boolean>>({ fractals: true, strokes: true, centers: true, segments: true, segmentCenters: true, candidates: true, unfinished: true, signals: true })
  const [selection, setSelection] = useState<{ symbol: string; timeframe: ChanTimeframe; date: string } | null>(null)
  const query = useQuery({ queryKey: QK.czscChart(symbol, timeframe, assetType), queryFn: () => api.czscChart(symbol, timeframe, assetType),
    enabled: !!symbol && validAsset, staleTime: 0, refetchOnMount: 'always', refetchOnWindowFocus: false, retry: false })
  const data = query.data
  const isIndex = assetType === 'index' || data?.asset_type === 'index'
  const structures = useMemo<ChartStructures>(() => ({
    segments: [...(layers.strokes ? data?.strokes ?? [] : []), ...(layers.unfinished ? (data?.unfinished ?? []).map(s => ({ ...s, dashed: true })) : []), ...(layers.segments ? (data?.segments ?? []).filter(s => layers.unfinished || s.confirmed).map(s => ({ ...s, color: '#14B8A6', width: 3 })) : [])],
    boxes: [...(layers.centers ? data?.centers ?? [] : []), ...(layers.segmentCenters ? (data?.segment_centers ?? []).map(b => ({ ...b, color: 'rgba(20,184,166,0.14)', borderColor: '#14B8A6' })) : [])],
    points: [...(layers.fractals ? (data?.fractals ?? []).map(f => ({ ...f, label: f.kind === 'top' ? '顶分型' : '底分型' })) : []),
      ...(layers.candidates ? (data?.bsp_points ?? []).map(p => ({ date: p.date, price: p.price, label: `${p.level === 'bi' ? '笔' : '段'}${p.types.join('/')} ${p.status === 'candidate' ? '候选' : '确认'}`, description: `${p.level === 'bi' ? '笔级' : '线段级'} ${p.types.map(t => CHAN_BSP_LABELS[t] ?? t).join('/')} ${p.is_buy ? '买' : '卖'} · ${p.status === 'candidate' ? '候选，可能变化' : `确认于 ${p.confirmed_at}`} · 首次出现 ${p.first_seen_at}`, candidate: p.status === 'candidate', above: !p.is_buy })) : []),
      ...(layers.signals ? (data?.signals ?? []).map(s => ({ date: s.date, price: s.price, label: cnSignal(s.signal_id).replace('chan.py ', ''), description: cnSignal(s.signal_id), signal: true, above: s.signal_id.endsWith('_sell') })) : [])],
  }), [data, layers])
  const selectedDate = selection?.symbol === symbol && selection.timeframe === timeframe ? selection.date : null
  const visibleEvents = (data?.signals ?? []).filter(e => !selectedDate || e.date === selectedDate).slice().reverse()

  return <div className="mx-auto max-w-[1600px] space-y-4 p-3 pt-14 md:p-6">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-lg font-semibold text-foreground">chan.py {periodLabel}结构图</h1><p className="mt-1 text-xs text-muted">已结束 K 线 · 结构快照、候选与确认时间</p></div><Link to={isIndex ? `/indices?symbol=${encodeURIComponent(symbol)}` : '/watchlist'} className="text-xs text-accent hover:underline">{isIndex ? '返回指数' : '返回自选'}</Link></div>
    <StockFinancialSearch onSelect={(next, _name, type) => { const nextAsset = type === 'index' ? 'index' : 'stock'; setParams({ symbol: next, asset_type: nextAsset, timeframe: nextAsset === 'index' && minute ? '1d' : timeframe }); setSelection(null) }} assetTypes="stock,index" placeholder="输入股票或指数代码、名称" />
    <div role="group" aria-label="图表周期" className="flex flex-wrap gap-2">{(Object.entries(PERIODS) as [ChanTimeframe, string][]).map(([period, label]) => <button key={period} type="button" aria-pressed={timeframe === period} disabled={isIndex && period.endsWith('m')} title={isIndex && period.endsWith('m') ? '指数仅支持日线和周线' : undefined} onClick={() => { setParams({ ...(symbol ? { symbol } : {}), timeframe: period, ...(assetType || data?.asset_type ? { asset_type: assetType ?? data!.asset_type } : {}) }); setSelection(null) }} className={`rounded-btn border px-3 py-1.5 text-xs disabled:opacity-40 ${timeframe === period ? 'border-accent bg-accent/10 text-accent' : 'border-border text-secondary'}`}>{label}</button>)}</div>
    {!validPeriod && <p role="status" className="text-xs text-warning">地址中的周期无效，已显示日线。</p>}{!validAsset && <p role="alert" className="text-xs text-warning">地址中的资产类型无效，请重新选择。</p>}{isIndex && <p className="text-xs text-muted">指数仅支持日线和周线；历史覆盖以本地数据为准。</p>}{timeframe === '1w' && <p className="text-xs text-muted">周线在北京时间周五 15:00 后纳入，节假日短周也采用此边界。</p>}
    {!symbol && <div role="status" className="rounded-card border border-border p-8 text-center text-sm text-muted">搜索股票或指数，查看结构与确认事件。</div>}
    {symbol && <><div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-base font-medium text-foreground">{data?.name ?? symbol} <span className="font-mono text-xs text-muted">{symbol}</span></h2>{data?.cutoff && <p className="mt-1 text-xs text-muted">行情截止 {data.cutoff} · 成交量：{data.volume_unit ?? '手'} · 成交额：{data.amount_unit ?? '元'}</p>}</div><button type="button" onClick={() => query.refetch()} disabled={query.isFetching} className="inline-flex items-center gap-1.5 rounded-btn border border-border px-3 py-1.5 text-xs disabled:opacity-50"><RefreshCw size={13} className={query.isFetching ? 'animate-spin' : ''} />{query.isFetching ? '刷新中' : '刷新'}</button></div>
    {minute && <p role="status" className="text-xs text-muted">分钟周期仅读取本机已存行情；此页面不触发数据采集或同步。</p>}{query.isPending && <div role="status" className="p-10 text-center text-muted">正在读取已结束的{periodLabel} K 线并计算结构…</div>}{query.isError && <div role="alert" className="rounded-card border border-warning/30 bg-warning/10 p-3 text-sm text-warning">{data ? '刷新失败，当前保留上次结果。' : ''}{query.error.message}</div>}{data && data.status !== 'ready' && <div role="status" className="rounded-card border border-border p-6 text-sm text-muted">{data.reason}{data.status === 'empty' && <Link to="/data" className="ml-2 text-accent underline">前往数据管理</Link>}{data.status === 'unavailable' && <p className="mt-2">{data.unavailable_reason === 'unsupported_timeframe' ? '当前标的暂不支持该周期。' : '请检查 chan.py 图表组件是否可用。'}</p>}</div>}
    {data?.status === 'ready' && <><div className="rounded-card border border-border bg-surface"><div className="flex flex-wrap gap-x-4 gap-y-2 border-b border-border p-3">{(Object.entries(LAYERS) as [keyof typeof LAYERS, string][]).map(([key, label]) => <label key={key} className="flex cursor-pointer items-center gap-1.5 text-xs text-secondary"><input type="checkbox" checked={layers[key]} onChange={() => setLayers(prev => ({ ...prev, [key]: !prev[key] }))} />{label}</label>)}</div><div className="p-1 sm:p-3">{!!data.rows?.length ? <EChartsCandlestick key={`${symbol}-${timeframe}`} data={data.rows} structures={structures} stockInfo={{ name: data.name }} height={650} visibleBars={250} showMA={false} activeIndicators={INDICATORS} volumeCompare={VOLUME_COMPARE} onDateClick={day => setSelection({ symbol, timeframe, date: day })} /> : <p role="status" className="p-8 text-center text-muted">行情无效，暂无可绘制的 K 线。</p>}</div><div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border px-3 py-2 text-[11px] text-muted"><span>蓝线：笔 · 青线：线段</span><span>紫色区域：笔中枢 · 青色区域：线段中枢</span><span>空心三角：候选买卖点</span><span>拖动/缩放 · 点击 K 线查看事件</span></div></div>
    <p className="text-xs leading-6 text-muted">{data.price_basis} · 本地{periodLabel} {data.input_start} 至 {data.input_end}，{data.input_count} 根（目标 {data.analysis_bars} 根）。{data.structure_start ? `当前保留结构始于 ${data.structure_start}。` : '当前样本尚未形成可展示的笔结构。'}结构为行情截止时点快照，结构端点不等于识别时间；候选可变化，算法确认不代表严格缠论买卖点或实际成交。{minute ? '分钟采用北京时间结束标签。' : '本地日线未提供逐根定版标识，尾日以盘后同步校正为准。'}{data.refreshed_at && `刷新于 ${new Date(data.refreshed_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}（北京时间）。`}</p>
    {(data.input_count ?? 0) < data.analysis_bars && <p role="status" className="text-xs text-warning">当前历史不足 {data.analysis_bars} 根，部分结构或确认信号可能无法计算。<Link to="/data" className="ml-2 text-accent underline">前往数据管理同步行情</Link></p>}
    {!!data.invalid_dates?.length && <p role="status" className="text-xs text-warning">{data.invalid_dates.length} 根无效 K 线已中断结构；未跨缺口连接：{data.invalid_dates.join('、')}</p>}
    <details className="rounded-card border border-border p-3 text-xs text-muted"><summary className="cursor-pointer">信号可计算性与口径 · chan.py {data.version}</summary><p className="mt-2">首次可计算只建立基线；缩放不会重算。历史起点、数据修订或预热范围不同可能导致结果差异。</p>{data.coverage?.signals.map(s => <p className="mt-1" key={s.signal_id}>{cnSignal(s.signal_id)}：可计算 {s.ready_rows}，不可计算 {s.unavailable_rows}{Object.entries(s.reasons).map(([r, n]) => `；${STRUCTURE_COVERAGE_REASON_LABELS[r] ?? r} ${n}`).join('')}</p>)}</details>
    <section className="rounded-card border border-border p-3"><div className="mb-2 flex items-center justify-between gap-2"><h3 className="text-sm text-foreground">确认事件{selectedDate ? ` · ${selectedDate}` : ''}</h3>{selectedDate && <button type="button" onClick={() => setSelection(null)} className="text-xs text-accent">查看全部</button>}</div>{!visibleEvents.length && <p className="text-xs text-muted">{selectedDate ? '该 K 线没有首次触发记录。' : '当前范围没有首次触发记录。'}</p>}<div className="max-h-64 space-y-1 overflow-auto">{visibleEvents.map(e => <div key={e.event_id ?? `${e.date}-${e.signal_id}`} className="flex flex-wrap justify-between gap-x-4 gap-y-1 rounded bg-elevated px-2 py-1.5 text-xs"><span className="font-mono text-muted">确认 {e.confirmed_at ?? e.date} · 端点 {e.endpoint_at ?? '—'} · 首见 {e.first_seen_at ?? '—'}</span><span className="text-secondary">{cnSignal(e.signal_id)}</span><span className="font-mono text-foreground">确认 K 线收盘 {e.price.toFixed(2)}</span></div>)}</div></section></>}
    </>}
  </div>
}
