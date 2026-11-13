import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import * as echarts from 'echarts'
import { Zap } from 'lucide-react'
import { api } from '@/lib/api'
import { useChartTheme } from '@/lib/theme'
import type {
  DialogDecor, MiniSpec, RecipeSlotProps, ResearchBar,
  ResearchRecord, ResearchRecipe, ResearchPage, ResearchZoom,
} from '../shared/types'

/**
 * 「打板接力」研究说明卡 —— 第 4 个标签。
 *
 * 与其它三张卡的不同 (也是验证工作台能不能接住「形态不同的研究」):
 *  - 数据是一次算好的离线快照 (public/daban_data.json), 不走业务接口、不点「重新计算」。
 *  - 顶部总账 / 一条条加限制表 = 基于全量 12 万笔前端实时重算 (准确, 可任意组合)。
 *  - 列表也是全量 12 万笔本身, 没有任何抽样: 点某一天, 那天的每一笔都在, 一条不少。
 *  - K 线不塞进 JSON (全量会到 40MB), 改成翻到哪页就按行号向后端要哪页的窗口。
 *  - 放大框复用现成日线接口拉真实前后一年 K 线。
 *
 * 所有差异都写在本卡里, 壳 (Workbench.tsx) 一行不改。
 */

// ---- 数据契约 (与 export_daban.py 对齐) ----
interface Meta {
  title: string
  n_all: number
  start: string
  exit_modes: Record<string, string>
  phases: string[]
  phase_names: string[]
  states: string[]
  state_names: string[]
  pos_names?: string[]      // 板位名, 如 ["首板","2板","3板"]
  pos_counts?: number[]     // 各板位全量笔数
  symbols: string[]
  name_list: string[]
  calendar: string[]
  win_before: number
  win_after: number
  stats_cols: string[]
  ohlc_n: number
  stop_loss: number
  caveats: string[]
}
interface DabanData {
  meta: Meta
  baseline: Record<string, { n: number; win: number; mean: number; median: number; total: number; p10: number; p90: number }>
  scan: Record<string, { base: unknown; rows: { cond: string; n: number; win: number; mean: number; median: number; d_mean: number; d_win: number }[] }>
  /** 全量交易, 每行 = stats_cols; 行号就是 K 线窗口在后端的下标 */
  stats: number[][]
}
interface Stat { n: number; win: number; mean: number; median: number; best: number; worst: number }

// 缓存: JSON 只解析一次
let DATA_PROMISE: Promise<DabanData> | null = null
function loadData(): Promise<DabanData> {
  if (!DATA_PROMISE) {
    DATA_PROMISE = fetch('/daban_data.json').then(r => {
      if (!r.ok) throw new Error('打板数据没读到')
      return r.json()
    })
  }
  return DATA_PROMISE
}

// ---- 列索引 ----
const SI: Record<string, number> = {}
function ensureIdx(data: DabanData) {
  if (SI.r_t1 != null) return
  data.meta.stats_cols.forEach((c, i) => { SI[c] = i })
}
const RET_KEYS = ['r_t1', 'r_t2', 'r_t3', 'r_t5', 'r_sl', 'r_3red', 'r_trail']
const RET_SHORT: Record<string, string> = {
  r_t1: '次日收盘卖', r_t2: '第2日卖', r_t3: '第3日卖', r_t5: '拿满5日',
  r_sl: '亏5%就割', r_3red: '三日不红就走', r_trail: '回落3%止盈',
}
const MISSING = -9999

const pct = (v: number) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`
const num = (v: number) => v.toLocaleString('zh-CN')
// 板位 -> 中文标签 (首板 / 第2板 / 第3板), 用于卡片与放大框文案
const bpLabel = (bp: number) => (bp === 1 ? '首板' : bp === 6 ? '>5板' : `第${bp}板`)

// 全量统计: 按出场模式 + 限制条件, 从 stats 实时重算
function summarize(data: DabanData, mode: string, filters: Filters): Stat {
  ensureIdx(data)
  const ri = SI[mode]
  const rows = data.stats
  let n = 0, win = 0, sum = 0, best = -Infinity, worst = Infinity
  const vals: number[] = []
  for (let k = 0; k < rows.length; k++) {
    const r = rows[k]
    if (!passFilter(r, filters)) continue
    const v = r[ri]
    if (v === MISSING || !isFinite(v)) continue
    const ret = v / 10000
    n += 1
    if (ret > 0) win += 1
    sum += ret
    if (ret > best) best = ret
    if (ret < worst) worst = ret
    vals.push(ret)
  }
  vals.sort((a, b) => a - b)
  const median = n ? (n % 2 ? vals[(n - 1) >> 1] : (vals[n / 2 - 1] + vals[n / 2]) / 2) : 0
  return { n, win: n ? win / n : 0, mean: n ? sum / n : 0, median, best: n ? best : 0, worst: n ? worst : 0 }
}

interface Filters {
  year: number          // 0 = 不限
  phase: string         // '' = 不限; 其余为 phase id
  state: string         // '' = 不限
  bp: number            // 板位: 1=首板 2=2板 3=3板, 0 = 不限(全看)
  minLu: number         // 涨停家数下限, 0 = 不限
  minPromo: number      // 晋级率下限 (千分比原值 ×1000), 0 = 不限
  minSeal: number       // 封板率下限 (×1000), 0 = 不限
  maxMc: number         // 全场最高连板上限, 0 = 不限
  minCap: number        // 市值下限(亿), 0 = 不限
  maxGap: number        // 隔夜高开上限(%), 0 = 不限; 存储为百分数
  onlyPhaseHot: boolean // 点火+主升才买(Phase0已证伪, 默认关)
  // 下面三个来自「点柱子选某一天/某一月」: day 只用于显示, dFrom/dTo 是 calendar 下标区间(-1 = 不限)
  day: string
  dFrom: number
  dTo: number
}

function defFilters(): Filters {
  return {
    year: 0, phase: '', state: '', bp: 1, minLu: 0, minPromo: 0, minSeal: 0,
    maxMc: 0, minCap: 0, maxGap: 0, onlyPhaseHot: false,
    day: '', dFrom: -1, dTo: -1,
  }
}

function passFilter(r: number[], f: Filters): boolean {
  const g = (c: string) => r[SI[c]]
  // 板位筛选 (首板/2板/3板...), 默认只看重首板, 与改前视图一致
  if (f.bp && g('bp') !== f.bp) return false
  // 图上点选的某一天/某一月
  if (f.dFrom >= 0) {
    const di = g('di')
    if (!(di >= f.dFrom && di <= f.dTo)) return false
  }
  if (f.year && g('yr') < f.year) return false
  if (f.phase) {
    const pi = g('ph')
    if (pi < 0 || pi > 5) return false
    // 冷相位 = ice(0)/ebb(5)
    if (f.phase === 'cold' && pi !== 0 && pi !== 5) return false
    else if (f.phase !== 'cold' && f.phase !== String(pi)) return false
  }
  if (f.onlyPhaseHot) {
    const pi = g('ph')
    if (pi !== 2 && pi !== 3) return false   // ignite / rally
  }
  if (f.state) {
    const si = g('st')
    if (f.state === 'strong' && si !== 0) return false
    if (f.state === 'notweak' && si === 2) return false
  }
  const chk = (c: string, ok: (v: number) => boolean) => {
    const v = g(c)
    return v !== MISSING && isFinite(v) && ok(v)
  }
  if (f.minLu && !chk('lu', v => v >= f.minLu)) return false
  if (f.minPromo && !chk('promo', v => v >= f.minPromo)) return false
  if (f.minSeal && !chk('seal', v => v >= f.minSeal)) return false
  if (f.maxMc && !chk('mc', v => v <= f.maxMc)) return false
  if (f.minCap && !chk('mktcap', v => v / 10 >= f.minCap)) return false
  if (f.maxGap && !chk('gap', v => v / 100 <= f.maxGap)) return false
  return true
}

// ---- 限制条件清单 (用于「一条条加限制」表 + 顶部开关) ----
interface LimitDef { key: string; label: string; apply: (f: Filters) => Filters }
const LIMITS: LimitDef[] = [
  { key: 'minLu30', label: '当天涨停 ≥ 30 家', apply: f => ({ ...f, minLu: 30 }) },
  { key: 'minLu50', label: '当天涨停 ≥ 50 家', apply: f => ({ ...f, minLu: 50 }) },
  { key: 'minLu80', label: '当天涨停 ≥ 80 家', apply: f => ({ ...f, minLu: 80 }) },
  { key: 'promo30', label: '晋级率 ≥ 30%', apply: f => ({ ...f, minPromo: 300 }) },
  { key: 'promo40', label: '晋级率 ≥ 40%', apply: f => ({ ...f, minPromo: 400 }) },
  { key: 'seal70', label: '封板率 ≥ 70%', apply: f => ({ ...f, minSeal: 700 }) },
  { key: 'seal80', label: '封板率 ≥ 80%', apply: f => ({ ...f, minSeal: 800 }) },
  { key: 'mc7', label: '全场最高板 ≤ 7', apply: f => ({ ...f, maxMc: 7 }) },
  { key: 'mc5', label: '全场最高板 ≤ 5', apply: f => ({ ...f, maxMc: 5 }) },
  { key: 'cap50', label: '市值 ≥ 50 亿', apply: f => ({ ...f, minCap: 50 }) },
  { key: 'cap100', label: '市值 ≥ 100 亿', apply: f => ({ ...f, minCap: 100 }) },
  { key: 'gap3', label: '隔夜高开 ≤ 3% 才买', apply: f => ({ ...f, maxGap: 3 }) },
  { key: 'gap1', label: '隔夜高开 ≤ 1% 才买', apply: f => ({ ...f, maxGap: 1 }) },
  { key: 'state_strong', label: '只在大盘「强」时买', apply: f => ({ ...f, state: 'strong' }) },
  { key: 'state_notweak', label: '避开大盘「弱」', apply: f => ({ ...f, state: 'notweak' }) },
  { key: 'phase_cold', label: '只在冷相位(冰点/退潮)买', apply: f => ({ ...f, phase: 'cold' }) },
  { key: 'phase_hot', label: '点火+主升才买(已被证伪)', apply: f => ({ ...f, onlyPhaseHot: true }) },
  { key: 'y2022', label: '只看 2022 年起', apply: f => ({ ...f, year: 2022 }) },
  { key: 'y2023', label: '只看 2023 年起', apply: f => ({ ...f, year: 2023 }) },
]

// ---- 按日统计图: 全量 stats 按「涨停当天」归集 ----
// from/to = 该柱子覆盖的 calendar 下标区间, 点柱子后拿它去筛选(月视图就是一个月的区间)
interface DayPoint { label: string; n: number; win: number; mean: number; from: number; to: number }

/**
 * 用全量统计行按事件日归集, 与顶部总账同源(准确), 不是拿抽样案例凑。
 * bucket=day 用交易日, month 用年月。当前板位与所有限制都生效。
 */
function dailyPoints(data: DabanData, mode: string, filters: Filters, bucket: 'day' | 'month'): DayPoint[] {
  ensureIdx(data)
  const ri = SI[mode]
  const di = SI.di
  if (di == null) return []
  const cal = data.meta.calendar
  const rows = data.stats
  const acc = new Map<string, { n: number; win: number; sum: number; from: number; to: number }>()
  for (let k = 0; k < rows.length; k++) {
    const r = rows[k]
    if (!passFilter(r, filters)) continue
    const v = r[ri]
    if (v === MISSING || !isFinite(v)) continue
    const idx = r[di]
    const d = cal[idx]
    if (!d) continue
    const key = bucket === 'month' ? d.slice(0, 7) : d
    let a = acc.get(key)
    if (!a) { a = { n: 0, win: 0, sum: 0, from: idx, to: idx }; acc.set(key, a) }
    else { if (idx < a.from) a.from = idx; if (idx > a.to) a.to = idx }
    const ret = v / 10000
    a.n += 1
    if (ret > 0) a.win += 1
    a.sum += ret
  }
  return [...acc.keys()].sort().map(key => {
    const a = acc.get(key)!
    return { label: key, n: a.n, win: a.n ? a.win / a.n : 0, mean: a.n ? a.sum / a.n : 0, from: a.from, to: a.to }
  })
}

const BULL = '#C74040'
const MEAN_LINE = '#E2B15A'

/** 打板接力自有的小部件: 每天(或每月)发生多少笔、平均一笔赚亏多少。 */
function DailyChart({ data, mode, filters, bucket, setBucket, onCollapse, onSelect }: {
  data: DabanData
  mode: string
  filters: Filters
  bucket: 'day' | 'month'
  setBucket: (b: 'day' | 'month') => void
  onCollapse: () => void
  /** 点柱子 -> 把这一天(或这一月)加进筛选; 传 null 表示清除 */
  onSelect: (p: DayPoint | null) => void
}) {
  const ct = useChartTheme()
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  // 图本身始终画「未按单日筛选」的分布, 否则点一下柱子就只剩一根柱子了;
  // 选中的那天只做高亮 + 去筛下面的列表/总账。
  const points = useMemo(
    () => dailyPoints(data, mode, { ...filters, day: '', dFrom: -1, dTo: -1 }, bucket),
    [data, mode, filters, bucket],
  )
  // 点击回调放 ref, 避免每次 filters 变化都重挂事件
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const selected = filters.dFrom >= 0 ? filters.day : ''

  useEffect(() => {
    if (!box.current || points.length === 0) {
      chart.current?.dispose()
      chart.current = null
      return
    }
    const inst = chart.current ?? echarts.init(box.current, undefined, { renderer: 'canvas' })
    chart.current = inst
    // 全历史 1900 多个交易日一屏塞不下, 按日默认只看最近约一年 (250 根), 想看更早拖滑块
    const zoomStart = Math.max(0, 100 - (250 / Math.max(points.length, 1)) * 100)
    inst.setOption({
      animation: false,
      grid: { left: 46, right: 50, top: 12, bottom: bucket === 'day' ? 40 : 20 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: ct.tooltipBg,
        borderColor: ct.tooltipBorder,
        textStyle: { color: ct.tooltipText, fontSize: 11 },
        formatter: (items: unknown) => {
          const arr = (Array.isArray(items) ? items : []) as { dataIndex?: number }[]
          const p = points[arr[0]?.dataIndex ?? 0]
          if (!p) return ''
          return `${p.label}<br/>笔数 <b>${p.n}</b><br/>赚钱比例 ${(p.win * 100).toFixed(1)}%<br/>平均一笔 ${pct(p.mean)}`
        },
      },
      xAxis: {
        type: 'category', data: points.map(p => p.label),
        axisLabel: { color: ct.text, fontSize: 10, hideOverlap: true },
        axisLine: { lineStyle: { color: ct.border } }, axisTick: { show: false },
      },
      yAxis: [
        {
          type: 'value', minInterval: 1,
          axisLabel: { color: ct.text, fontSize: 10 },
          splitLine: { lineStyle: { color: ct.grid } },
        },
        {
          type: 'value', position: 'right', scale: true,
          axisLabel: { color: ct.text, fontSize: 10, formatter: (v: number) => `${v.toFixed(1)}%` },
          splitLine: { show: false },
        },
      ],
      dataZoom: bucket === 'day'
        ? [
            // 两个 dataZoom 共用同一个可见窗口: 都给 start/end, 否则不带范围的那个会把窗口拉回全历史
            { type: 'inside', start: zoomStart, end: 100 },
            {
              type: 'slider', height: 14, bottom: 4, start: zoomStart, end: 100,
              borderColor: ct.border, textStyle: { color: ct.text, fontSize: 9 },
            },
          ]
        : [],
      series: [
        {
          name: '笔数', type: 'bar', yAxisIndex: 0, cursor: 'pointer',
          barMaxWidth: bucket === 'month' ? 24 : 9,
          // 选中的那根染成金色, 再点一次取消
          data: points.map(p => ({ value: p.n, itemStyle: { color: p.label === selected ? MEAN_LINE : BULL } })),
        },
        {
          name: '平均一笔', type: 'line', yAxisIndex: 1,
          symbol: 'none', lineStyle: { width: 1.2, color: MEAN_LINE },
          markLine: {
            silent: true, symbol: 'none',
            lineStyle: { color: ct.border, type: 'dashed', width: 1 },
            data: [{ yAxis: 0 }], label: { show: false },
          },
          data: points.map(p => +(p.mean * 100).toFixed(3)),
        },
      ],
    }, true)
    inst.off('click')
    inst.on('click', (params: { componentType?: string; seriesIndex?: number; dataIndex?: number }) => {
      if (params.componentType !== 'series' || params.seriesIndex !== 0) return
      const p = points[params.dataIndex ?? -1]
      if (!p) return
      onSelectRef.current(selected === p.label ? null : p)
    })
    const onResize = () => inst.resize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [points, bucket, ct, selected])

  useEffect(() => () => { chart.current?.dispose(); chart.current = null }, [])

  const total = points.reduce((s, p) => s + p.n, 0)

  return (
    <div className="border-t border-border px-5 py-1.5">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="text-xs text-secondary">按日统计：每天多少笔 · 平均赚亏</span>
        <button type="button" onClick={() => setBucket('day')}
          className={`h-6 rounded-btn px-2 text-[11px] ${bucket === 'day' ? 'bg-elevated text-foreground' : 'text-muted hover:bg-elevated'}`}>按日</button>
        <button type="button" onClick={() => setBucket('month')}
          className={`h-6 rounded-btn px-2 text-[11px] ${bucket === 'month' ? 'bg-elevated text-foreground' : 'text-muted hover:bg-elevated'}`}>按月</button>
        <span className="text-[11px] text-muted">
          共 {num(total)} 笔 · 红柱=每天笔数（左轴）· 黄线=平均一笔（右轴）· 点柱子只看那天
          {bucket === 'day' && ' · 默认最近一年，拖滑块往前看'}
        </span>
        {selected && (
          <button type="button" onClick={() => onSelect(null)}
            className="h-6 rounded-btn bg-elevated px-2 text-[11px] text-accent">已选 {selected} · 清除 ✕</button>
        )}
        <button type="button" onClick={onCollapse}
          className="ml-auto h-6 rounded-btn px-2 text-[11px] text-muted hover:bg-elevated hover:text-foreground">收起 ▲</button>
      </div>
      {points.length === 0
        ? <div className="flex h-24 items-center justify-center text-xs text-muted">这个条件下没有交易。</div>
        : <div ref={box} className="h-36 w-full" />}
    </div>
  )
}

// 一行全量交易 + 后端返回的 K 线窗口 -> ResearchRecord
function toRecord(data: DabanData, row: number[], id: number, ohlc?: number[]): ResearchRecord {
  ensureIdx(data)
  const m = data.meta
  const sym = row[SI.sym]
  const symbol = m.symbols[sym] ?? String(sym)
  const name = m.name_list[sym] ?? symbol
  const tIdx = row[SI.di]
  const startDate = m.calendar[tIdx] ?? m.start
  // 买入日固定是涨停日之后那个交易日 (T+1 开盘买), 不用再单独存一列
  const buyDate = m.calendar[tIdx + 1] ?? startDate
  const bars: ResearchBar[] = []
  if (ohlc && ohlc.length) {
    const nb = m.win_before + m.win_after + 1
    for (let j = 0; j < nb; j++) {
      const date = m.calendar[tIdx - m.win_before + j]
      if (!date) continue
      const o = ohlc[j * 4], h = ohlc[j * 4 + 1]
      const l = ohlc[j * 4 + 2], c = ohlc[j * 4 + 3]
      if (o < 0 || h < 0 || l < 0 || c < 0) continue   // 停牌/缺失
      bars.push({ date, open: o / 100, high: h / 100, low: l / 100, close: c / 100 })
    }
  }
  const phaseName = m.phase_names[row[SI.ph]] ?? '—'
  return {
    id: `daban-${id}`,
    symbol, name,
    startDate, endDate: buyDate,
    bars,
    raw: { row, buyDate, tIdx, phaseName },
  }
}

// ---- 顶部小部件: 总账 + 限制开关 + 一条条加限制表 ----
function ExtraPanel({ ctx, setExtra }: RecipeSlotProps) {
  const dataQ = useQuery({ queryKey: ['research-workbench', 'daban', 'data'], queryFn: loadData, staleTime: Infinity })
  const data = dataQ.data
  const mode = (ctx.extra.mode as string) || 'r_t1'
  const filters = (ctx.extra.filters as Filters) || defFilters()
  // 详细分析默认收起, 把首屏高度让给下面的 K 线列表(用户反馈: 上面文字太多看不到图)
  const [showDetail, setShowDetail] = useState(false)
  // 按日统计图默认展开(用户点名要的功能), 但可一键收起把空间还给 K 线
  const [showChart, setShowChart] = useState(true)
  const bucket = (ctx.extra.bucket as 'day' | 'month') || 'day'

  const stat = useMemo(() => data ? summarize(data, mode, filters) : null, [data, mode, filters])
  // 干净基线 = 只按当前板位(和图上点选的那一天)、其它不限制。总账对照卡、限制表「比没限制」
  // 都用这一个基准, 三处口径完全一致, 不会出现「没加限制」两个不同数字。一字板不再剔除, 始终计入。
  const clean = useMemo(
    () => data ? summarize(data, mode, { ...defFilters(), bp: filters.bp, dFrom: filters.dFrom, dTo: filters.dTo }) : null,
    [data, mode, filters.bp, filters.dFrom, filters.dTo],
  )

  const limitRows = useMemo(() => {
    // 收起时不算这 19 行, 展开再算(全量扫描, 很快); 限制表也跟随板位
    if (!data || !clean || !showDetail) return []
    return LIMITS.map(l => {
      const s = summarize(data, mode, l.apply({ ...defFilters(), bp: filters.bp, dFrom: filters.dFrom, dTo: filters.dTo }))
      return { ...l, s, active: filterKeyOf(filters, l.key), dMean: s.mean - clean.mean }
    })
  }, [data, mode, filters, clean, showDetail])

  if (dataQ.isLoading) return <div className="border-b px-5 py-3 text-xs text-muted">正在读取打板数据（约 12MB）…</div>
  if (dataQ.isError || !data) return <div className="border-b px-5 py-3 text-xs text-danger">打板数据没读到，刷新页面重试。</div>

  const m = data.meta

  return (
    <div className="border-b border-border bg-surface/50">
      {/* 总账 */}
      <div className="grid grid-cols-3 gap-2 px-5 py-2 md:grid-cols-6">
        <Card label="符合条件" value={num(stat?.n ?? 0)}
          sub={filters.day
            ? `只看 ${filters.day} · ${bpLabel(filters.bp)}`
            : `${bpLabel(filters.bp)} 共 ${(m.pos_counts?.[filters.bp - 1] ?? m.n_all).toLocaleString('zh-CN')} 笔`} />
        <Card label="赚钱比例" value={stat ? (stat.win * 100).toFixed(1) + '%' : '—'} sub="收益 > 0" tone={stat && stat.win >= 0.5 ? 'good' : 'bad'} />
        <Card label="平均一笔" value={stat ? pct(stat.mean) : '—'} sub={`中位 ${stat ? pct(stat.median) : '—'}`} tone={(stat?.mean ?? 0) >= 0 ? 'good' : 'bad'} />
        <Card label="最好 / 最差" value={stat ? pct(stat.best) : '—'} sub={stat ? `最差 ${pct(stat.worst)}` : '—'} tone="good" />
        <Card label="对照：不加限制" value={clean ? (clean.win * 100).toFixed(1) + '%' : '—'} sub={`平均 ${clean ? pct(clean.mean) : '—'}`} />
        <Card label="出场规则" value={RET_SHORT[mode] ?? mode} sub={m.exit_modes[mode] ?? ''} />
      </div>

      {/* 一行口径 + 详细分析折叠入口 */}
      <div className="flex items-center gap-2 border-t border-border px-5 py-1 text-[11px] text-muted">
        <span className="min-w-0 truncate">
          {filters.day && <b className="text-accent">当前只看 {filters.day} 这一天（点柱子上方的「清除」可取消）· </b>}
          上面数字和下面的列表<b className="text-secondary">都是全部 {num(m.n_all)} 笔交易本身</b>，没有抽样 —— 点某一天，那天的每一笔都在，一条不少。一字板不再剔除、始终计入（卡片上标「一字买不进」的是涨停当天/次日一字封死、实际根本买不到的，数字已含它们）。
        </span>
        <button type="button" onClick={() => setShowDetail(v => !v)}
          className="ml-auto shrink-0 rounded-btn px-2 py-0.5 text-[11px] text-accent hover:bg-elevated">
          {showDetail ? '收起 ▲' : `逐条试限制(${LIMITS.length}) / 口径与坑 ▼`}
        </button>
      </div>

      {/* 限制开关 */}
      <div className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-1.5">
        <span className="text-xs text-secondary">打：</span>
        <select className="h-7 rounded-btn bg-elevated px-2 text-xs text-foreground" value={filters.bp}
          onChange={e => setExtra({ filters: { ...filters, bp: Number(e.target.value) } })}>
          {(m.pos_names ?? ['首板', '2板', '3板']).map((nm, i) => (
            <option key={i} value={i + 1}>
              {nm}（{(m.pos_counts?.[i] ?? 0).toLocaleString('zh-CN')}）
            </option>
          ))}
        </select>
        <span className="text-xs text-secondary">限制：</span>
        <select className="h-7 rounded-btn bg-elevated px-2 text-xs text-foreground" value={filters.phase}
          onChange={e => setExtra({ filters: { ...filters, phase: e.target.value } })}>
          <option value="">市场情绪：不限</option>
          {m.phases.map((p, i) => <option key={p} value={String(i)}>{m.phase_names[i]}</option>)}
          <option value="cold">只在冰点/退潮</option>
        </select>
        <select className="h-7 rounded-btn bg-elevated px-2 text-xs text-foreground" value={filters.state}
          onChange={e => setExtra({ filters: { ...filters, state: e.target.value } })}>
          <option value="">大盘状态：不限</option>
          <option value="strong">只在大盘强</option>
          <option value="notweak">避开大盘弱</option>
        </select>
        <select className="h-7 rounded-btn bg-elevated px-2 text-xs text-foreground" value={filters.year}
          onChange={e => setExtra({ filters: { ...filters, year: Number(e.target.value) } })}>
          <option value="0">年份：全历史</option>
          {[2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026].map(y => <option key={y} value={y}>{y} 年起</option>)}
        </select>
        <button type="button" onClick={() => setExtra({ filters: defFilters() })}
          className="h-7 rounded-btn px-2 text-xs text-muted hover:bg-elevated hover:text-foreground">清空限制</button>
      </div>

      {/* 按日统计图: 每天多少笔 + 平均赚亏 (全量, 跟随板位与限制); 点柱子只看那一天 */}
      {showChart ? (
        <DailyChart data={data} mode={mode} filters={filters} bucket={bucket}
          setBucket={b => setExtra({ bucket: b, filters: { ...filters, day: '', dFrom: -1, dTo: -1 } })}
          onCollapse={() => setShowChart(false)}
          onSelect={p => setExtra({
            filters: p
              ? { ...filters, day: p.label, dFrom: p.from, dTo: p.to }
              : { ...filters, day: '', dFrom: -1, dTo: -1 },
          })}
        />
      ) : (
        <div className="border-t border-border px-5 py-1">
          <button type="button" onClick={() => setShowChart(true)}
            className="rounded-btn px-2 py-0.5 text-[11px] text-accent hover:bg-elevated">展开按日统计图 ▼</button>
        </div>
      )}

      {/* 详细分析: 默认收起, 点上面「逐条试限制」按钮展开 */}
      {showDetail && (
        <>
      {/* 一条条加限制表 */}
      <div className="border-t border-border px-5 py-2">
        <div className="mb-1 text-[11px] text-secondary">
          逐条试：每行 = 别的都不加、只加这一条（对比「不加限制」的全量基线）；点一行即套用，叠加用上面那排开关。
        </div>
        <div className="max-h-52 overflow-auto rounded border border-border">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-elevated text-muted">
              <tr>
                <th className="px-2 py-1.5 text-left font-normal">只加这一条限制</th>
                <th className="px-2 py-1.5 text-right font-normal">还剩几笔</th>
                <th className="px-2 py-1.5 text-right font-normal">赚钱比例</th>
                <th className="px-2 py-1.5 text-right font-normal">平均一笔</th>
                <th className="px-2 py-1.5 text-right font-normal">比没限制</th>
              </tr>
            </thead>
            <tbody>
              {limitRows.map(l => (
                <tr key={l.key} onClick={() => setExtra({ filters: l.apply(defFilters()) })}
                  className={`cursor-pointer border-t border-border/60 hover:bg-elevated ${l.active ? 'bg-accent/10' : ''}`}>
                  <td className="px-2 py-1">{l.label}{l.active && <span className="ml-1 text-accent">· 已用</span>}</td>
                  <td className="px-2 py-1 text-right font-mono text-muted">{num(l.s.n)}</td>
                  <td className="px-2 py-1 text-right">{(l.s.win * 100).toFixed(1)}%</td>
                  <td className={`px-2 py-1 text-right ${l.s.mean >= 0 ? 'text-bull' : 'text-bear'}`}>{pct(l.s.mean)}</td>
                  <td className={`px-2 py-1 text-right ${l.dMean >= 0 ? 'text-bull' : 'text-bear'}`}>{pct(l.dMean)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 前车之鉴 */}
      <div className="border-t border-border px-5 py-2">
        <div className="mb-1 text-[11px] font-medium text-secondary">别被数字骗（完整口径与坑）</div>
        <ul className="grid gap-0.5 text-[11px] leading-relaxed text-muted sm:grid-cols-2">
          {m.caveats.map((c, i) => <li key={i} className="flex gap-1"><span className="text-muted/60">{i + 1}.</span><span>{c}</span></li>)}
        </ul>
      </div>
        </>
      )}
    </div>
  )
}

function filterKeyOf(f: Filters, key: string): boolean {
  // 判断某个限制是否已生效 (用于高亮)
  const map: Record<string, (f: Filters) => boolean> = {
    minLu30: f => f.minLu === 30, minLu50: f => f.minLu === 50, minLu80: f => f.minLu === 80,
    promo30: f => f.minPromo === 300, promo40: f => f.minPromo === 400,
    seal70: f => f.minSeal === 700, seal80: f => f.minSeal === 800,
    mc7: f => f.maxMc === 7, mc5: f => f.maxMc === 5,
    cap50: f => f.minCap === 50, cap100: f => f.minCap === 100,
    gap3: f => f.maxGap === 3, gap1: f => f.maxGap === 1,
    state_strong: f => f.state === 'strong', state_notweak: f => f.state === 'notweak',
    phase_cold: f => f.phase === 'cold', phase_hot: f => f.onlyPhaseHot,
    y2022: f => f.year === 2022, y2023: f => f.year === 2023,
  }
  return map[key]?.(f) ?? false
}

function Card({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'good' | 'bad' }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-2.5 py-1.5">
      <div className="text-[10px] text-muted">{label}</div>
      <div className={`mt-0.5 truncate text-base font-semibold ${tone === 'good' ? 'text-bull' : tone === 'bad' ? 'text-bear' : 'text-foreground'}`} title={value}>{value}</div>
      {sub && <div className="mt-0.5 truncate text-[10px] text-muted" title={sub}>{sub}</div>}
    </div>
  )
}

export const dabanRecipe: ResearchRecipe = {
  id: 'daban',
  label: '打板接力',
  icon: Zap,

  subtitle: (page, ctx) => {
    const mode = (ctx.extra.mode as string) || 'r_t1'
    const bp = (ctx.extra.bp as number) || (ctx.extra.filters as Filters)?.bp || 1
    const nAll = page?.meta?.n_all ? num(page.meta.n_all as number) : '11 万多'
    const label = bpLabel(bp)
    return `${label}当天涨停买不到 → 次日开盘买进 → 再看怎么卖。列表就是全部 ${nAll} 笔交易本身，没有抽样；当前按「${RET_SHORT[mode]}」算收益（前复权、未扣费）。`
  },

  sortOptions: [
    { value: 'date', label: '首板日期' },
    { value: 'ret', label: '赚/亏多少' },
    { value: 'boards', label: '最终几连板(事后)' },
    { value: 'lu', label: '当天涨停家数' },
  ],
  // 默认按日期看真实交易(成功失败混杂), 不按收益降序 —— 否则首屏清一色极端盈利, 有幸存者偏差
  defaultSort: 'date',
  initialExtra: { mode: 'r_t1', filters: defFilters(), bucket: 'day' },
  queryExtra: extra => ({ mode: extra.mode, f: JSON.stringify(extra.filters) }),

  fetchPage: async (ctx): Promise<ResearchPage> => {
    const data = await loadData()
    ensureIdx(data)
    const filters = (ctx.extra.filters as Filters) || defFilters()
    const mode = (ctx.extra.mode as string) || 'r_t1'
    const ri = SI[mode]

    // 直接过滤全量交易, 不是抽样池 —— 点某一天, 那天的每一笔都在, 一条不少。
    const ids: number[] = []
    for (let i = 0; i < data.stats.length; i++) {
      const row = data.stats[i]
      if (!passFilter(row, filters)) continue
      const sym = data.meta.symbols[row[SI.sym]] ?? ''
      const name = data.meta.name_list[row[SI.sym]] ?? ''
      if (ctx.q && !(sym.includes(ctx.q) || name.includes(ctx.q))) continue
      const d = data.meta.calendar[row[SI.di]] ?? ''
      if (ctx.start && d < ctx.start) continue
      if (ctx.end && d > ctx.end) continue
      ids.push(i)
    }
    const val = (i: number) => {
      const row = data.stats[i]
      if (ctx.sort === 'date') return row[SI.di]
      if (ctx.sort === 'boards') return row[SI.boards]
      if (ctx.sort === 'lu') return row[SI.lu] ?? 0
      return row[ri]                          // 'ret' 按当前出场收益
    }
    ids.sort((a, b) => ctx.order === 'desc' ? val(b) - val(a) : val(a) - val(b))
    const total = ids.length
    const page = ids.slice(ctx.offset, ctx.offset + ctx.limit)

    // K 线不跟着 JSON 走(全量会到 40MB): 只取当前这一页的窗口, 一次请求。
    // 取不到就降级成没有缩略图 —— 列表本身是全量算的, 不该因为 K 线挂了整页打不开。
    let ohlcRows: number[][] = []
    if (page.length) {
      try {
        const res = await api.dabanResearchBars(page)
        ohlcRows = res.rows ?? []
      } catch {
        ohlcRows = []
      }
    }

    const stat = summarize(data, mode, filters)
    return {
      rows: page.map((i, k) => toRecord(data, data.stats[i], i, ohlcRows[k])),
      total,
      computed: true,
      meta: { n_all: data.meta.n_all, stat, mode },
    }
  },

  fetchZoom: async (rec): Promise<ResearchZoom> => {
    const data = await api.limitUpResearchZoom({ symbol: rec.symbol, start: rec.startDate, end: rec.startDate })
    return {
      bars: data.bars,
      fromDate: data.from_date, toDate: data.to_date,
      barsBefore: data.bars_before, barsAfter: data.bars_after,
    }
  },

  ExtraFilters: ({ ctx, setExtra }: RecipeSlotProps) => {
    const mode = (ctx.extra.mode as string) || 'r_t1'
    return (
      <label className="flex items-center gap-1 text-xs text-muted">
        怎么卖
        <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={mode}
          onChange={e => setExtra({ mode: e.target.value })}>
          {RET_KEYS.map(k => <option key={k} value={k}>{RET_SHORT[k]}</option>)}
        </select>
      </label>
    )
  },
  ExtraPanel,

  card: (rec, ctx) => {
    const mode = (ctx.extra.mode as string) || 'r_t1'
    const bp = (ctx.extra.bp as number) || (ctx.extra.filters as Filters)?.bp || 1
    const { row, buyDate, phaseName } = rec.raw as { row: number[]; buyDate: string; phaseName: string }
    const ret = row[SI[mode]] / 10000
    const boards = row[SI.boards]
    const lu = row[SI.lu]
    const yizi = row[SI.yizi_t] === 1 || row[SI.yizi_b] === 1
    const label = bpLabel(bp)
    return (
      <>
        <div className="flex items-baseline justify-between gap-2">
          <div className="min-w-0 truncate text-sm font-medium text-foreground">{rec.name || rec.symbol}</div>
          <div className={`shrink-0 text-sm font-semibold ${ret >= 0 ? 'text-bull' : 'text-bear'}`}>{pct(ret)}</div>
        </div>
        <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-muted">
          <span className="truncate font-mono">{rec.symbol}</span>
          <span>{label} {rec.startDate}</span>
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted">
          <span>次日 {buyDate} 开盘买</span>
          <span>· 涨停 {lu} 家</span>
          <span>· {phaseName}</span>
          <span title="最终连板数是买完之后才走出来的，不能拿来当买入条件">· 后来 <b className="text-secondary">{boards} 连板</b>（事后，不能选股）</span>
          {yizi && <span className="text-bear">· 一字买不进</span>}
        </div>
      </>
    )
  },

  mini: (rec) => {
    const { buyDate } = rec.raw as { buyDate: string }
    return {
      band: { start: rec.startDate, end: rec.startDate },
      shadeAfter: buyDate, shadeAfterInclusive: true,
      vlines: [
        { date: rec.startDate, color: '#E2B15A', dash: '3 2' },
        { date: buyDate, color: '#7AA5F0', dash: '2 2' },
      ],
    } as MiniSpec
  },

  dialogDecor: (rec, ctx, frame): DialogDecor => {
    const mode = (ctx.extra.mode as string) || 'r_t1'
    const bp = (ctx.extra.bp as number) || (ctx.extra.filters as Filters)?.bp || 1
    const { row, buyDate } = rec.raw as { row: number[]; buyDate: string }
    const ret = row[SI[mode]] / 10000
    const boards = row[SI.boards]
    const label = bpLabel(bp)
    return {
      subtitle: `${label} ${rec.startDate} · 次日 ${buyDate} 开盘买 · 按「${RET_SHORT[mode]}」${pct(ret)} · 后来走出 ${boards} 连板（事后）`,
      ranges: [{ start: rec.startDate, end: buyDate, label: `${label}→买入`, color: 'rgba(226,177,90,0.22)' }],
      markers: [
        { date: rec.startDate, kind: 'neutral', above: true, label: label, color: '#E2B15A' },
        { date: buyDate, kind: 'buy', above: true, label: '开盘买入', color: '#7AA5F0' },
      ],
      focus: frame === 'year' ? 'all' : { start: rec.startDate, end: buyDate, padding: 12 },
      chartKey: frame,
    }
  },

  frames: {
    spanLabel: '看这段', allLabel: '看全年',
    spanText: '当前对准涨停板到买入这一段。', allText: '当前是买入前后各一年，橙色是涨停板那天。',
  },
  zoomNouns: { beforeWindow: '涨停板前一年', spanNoun: '', afterWindow: '一年' },

  empty: (ctx) => {
    const f = (ctx.extra.filters as Filters) || defFilters()
    if (f.day) {
      return {
        title: `${f.day} 这天没有符合条件的交易`,
        hint: '这天本身有交易，但被上面的限制条件(板位/情绪/市值等)挡住了。点图上「清除 ✕」或放宽限制再看。',
      }
    }
    return { title: '这个条件下没有交易', hint: '放宽限制条件、清空年份或搜索词再试。列表就是全量交易本身，没有抽样。' }
  },
  pageSize: 12,
}
