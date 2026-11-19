import { useEffect, useMemo, useRef, useState } from 'react'
import { TagEvolution } from './TagEvolution'
import { SignalEvolution } from './SignalEvolution'
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
 *  - 数据不是离线快照: 点「重新计算」→ 后端按本地日线重算 CONFIG.start (2016-11-10) 起
 *    6 个板位的全部涨停记录 → GET /api/daban-research/dataset 返回 (没算过 computed=false)。
 *    和涨停段/连涨段一样, 算完会缓存下来, 不会因为行情更新自动重算。
 *  - 不再按 T+10 画图窗口裁掉首尾, 也不再剔除新股/异常记录: 列表就是全量涨停记录本身,
 *    没有抽样。唯一剔除的是末尾几天持有期没走完的涨停 (算不出收益, 留着只会充数)。
 *    极少数个股持有期停牌导致卖出日无价的, 收益留空 (-9999 = MISSING), 不参与胜率/均值/收益排序。
 *  - 顶部总账 / 一条条加限制表 / 按日图 = 基于全量 stats 前端实时重算 (可任意组合)。
 *  - K 线不塞进 JSON (全量会到 40MB), 改成翻到哪页就按行号向后端要哪页的窗口;
 *    后端按 clip 取窗口, 前端按交易日历跳过越界下标, 所以尾部只画真实交易日、少几根。
 *  - 放大框复用现成日线接口拉真实前后一年 K 线。
 *
 * 所有差异都写在本卡里, 壳 (Workbench.tsx) 一行不改。
 */

// ---- 数据契约 (与 export_daban.py 对齐) ----
interface Meta {
  title: string
  n_all: number
  start: string
  /** 涨停事件口径: "touch"=盘中最高价触板(含炸板), "close"=收盘封板 */
  limit_basis?: 'touch' | 'close'
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
  /** 收益留空的记录数 (个股持有期内停牌/数据缺失导致卖出日无价; 末尾持有期不足的已整条剔除) */
  n_no_ret?: number
  caveats: string[]
  /** 分类标签定义 (id/名称/分组/识别方式/判定时点/位号) */
  tag_defs?: TagDef[]
  /** 标签定义以代码为准; 此处非空表示缓存是按更小的标签集算的, bits 里的位掩码全为 0 是「没算过」而非「不命中」 */
  tag_stale?: { cached_count: number; code_count: number; bits: number[] } | null
}
/** 一条分类标签的定义; bit 是它在每笔多 word 位掩码里的位号 (word=bit//53, 偏移=bit%53) */
interface TagDef {
  id: string
  bit: number
  name: string
  group: string
  /** review = 含 T 日放量涨停/突破/吞没等决定性动作的完整形态, 只作复盘切片, 不能当买入信号 */
  timing: 'pre' | 'event' | 'buy' | 'review'
  rule: string
}
interface DabanData {
  /** 后端还没点过「重新计算」时为 false, 此时 stats 为空 */
  computed: boolean
  meta: Meta
  /** 全量交易, 每行 = stats_cols; 行号就是 K 线窗口在后端的下标 */
  stats: number[][]
  /**
   * 每笔一个多 word 位掩码 (后端 int64, 53 位/word), 行号与 stats 严格同序;
   * tags[k][w] 的第 b 位为 1 表示命中位号 w*53+b 的标签。旧缓存没标签时为空数组。
   */
  tags: number[][]
}
interface Stat { n: number; win: number; mean: number; median: number; best: number; worst: number }

// 缓存: 10MB 结果只请求/解析一次。数据契约由后端 daban_compute 保证, 故 unknown 后断言。
let DATA_PROMISE: Promise<DabanData> | null = null
function fetchData(): Promise<DabanData> {
  return (api.dabanResearchDataset() as Promise<DabanData>).catch(err => {
    DATA_PROMISE = null                    // 失败不要永久缓存, 允许刷新重试
    throw err
  })
}
function loadData(): Promise<DabanData> {
  if (!DATA_PROMISE) DATA_PROMISE = fetchData()
  return DATA_PROMISE
}
/** 点「重新计算」后换成新的请求 Promise: 列表和顶部面板共用它, 只发一次请求。 */
function refreshDabanData() {
  DATA_PROMISE = fetchData()
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

// ---- 分类标签位判定: 用算术而非 JS 位运算 ----
// 后端每笔记录给 TAG_WORDS 个 int64, 每个 word 装 53 位: JS number 只能精确表示到
// 2**53-1, 53 个位全置 1 恰好等于它, 所以「除以 2**bit 取整再判奇偶」在这个位宽内
// 是精确的; 1<<bit 在 bit≥31 就会溢出成负数, 不能用。
// 位号 bit 落在 word = floor(bit/53)、word 内偏移 bit%53; 标签超过 53 条就自动用到 word1。
const TAG_BITS_PER_WORD = 53
const POW2: number[] = Array.from({ length: TAG_BITS_PER_WORD }, (_, i) => Math.pow(2, i))
/** words = 一笔记录的多 word 掩码; 缺失的 word 一律按「不命中」处理, 不猜 */
const hasTag = (words: number[] | undefined, bit: number): boolean => {
  if (!words) return false
  const value = words[Math.floor(bit / TAG_BITS_PER_WORD)]
  if (value == null) return false
  return Math.floor(value / POW2[bit % TAG_BITS_PER_WORD]) % 2 === 1
}
/** AND 语义: 选中的每个标签位都必须在这一笔的掩码里 */
const allTagsHit = (words: number[] | undefined, bits: number[]): boolean =>
  bits.every(b => hasTag(words, b))
/** 没有标签缓存时的占位: 所有位都不命中, 勾了标签就筛不出, 不会误放行 */
const NO_TAGS: number[] = []

const pct = (v: number) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`
const num = (v: number) => v.toLocaleString('zh-CN')
// 板位 -> 中文标签; 0 = 不限板位(默认的全量视图)
const bpLabel = (bp: number) => (bp === 0 ? '全部板位' : bp === 1 ? '首板' : bp === 6 ? '>5板' : `第${bp}板`)

// 全量统计: 按出场模式 + 限制条件, 从 stats 实时重算
function summarize(data: DabanData, mode: string, filters: Filters): Stat {
  ensureIdx(data)
  const ri = SI[mode]
  const rows = data.stats
  // n = 符合筛选的全部涨停记录 (含收益留空的), 与列表条数、按日图柱高一致;
  // win/mean/median/best/worst 只基于收益已走完的样本 (nRet 作分母)。
  let n = 0, nRet = 0, win = 0, sum = 0, best = -Infinity, worst = Infinity
  const vals: number[] = []
  for (let k = 0; k < rows.length; k++) {
    const r = rows[k]
    if (!passFilter(r, filters, data.tags?.[k], mode)) continue
    n += 1
    const v = r[ri]
    if (v === MISSING || !isFinite(v)) continue
    const ret = v / 10000
    nRet += 1
    if (ret > 0) win += 1
    sum += ret
    if (ret > best) best = ret
    if (ret < worst) worst = ret
    vals.push(ret)
  }
  vals.sort((a, b) => a - b)
  const median = nRet ? (nRet % 2 ? vals[(nRet - 1) >> 1] : (vals[nRet / 2 - 1] + vals[nRet / 2]) / 2) : 0
  return { n, win: nRet ? win / nRet : 0, mean: nRet ? sum / nRet : 0, median,
    best: nRet ? best : 0, worst: nRet ? worst : 0 }
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
  zaban: number         // 炸板过滤: 0=全部 1=仅封板 2=仅炸板 (触板口径下才有意义)
  pnl: number           // 盈亏过滤: 0=全部 1=只看盈利 2=只看亏损 (按当前出场规则 mode 的收益判定)
  // 下面三个来自「点柱子选某一天/某一月」: day 只用于显示, dFrom/dTo 是 calendar 下标区间(-1 = 不限)
  day: string
  dFrom: number
  dTo: number
  /** 勾中的分类标签位号数组 (不是位掩码: 位号已超过 JS 位运算 32 位上限); 空 = 不勾(不过滤), 多个取交集 AND */
  tags: number[]
}

function defFilters(): Filters {
  // 默认全量: 不限板位、不加任何限制开关、不勾标签、不限日期 —— 打开页面就是全部交易。
  return {
    year: 0, phase: '', state: '', bp: 0, minLu: 0, minPromo: 0, minSeal: 0,
    maxMc: 0, minCap: 0, maxGap: 0, onlyPhaseHot: false, zaban: 0, pnl: 0,
    day: '', dFrom: -1, dTo: -1, tags: [],
  }
}

function passFilter(r: number[], f: Filters, words: number[] = NO_TAGS, mode = 'r_t1'): boolean {
  // 分类标签: 勾多个取交集 (AND)。旧缓存没有标签时 words 为空, 勾了自然筛不出, 不会误放行。
  if (f.tags.length && !allTagsHit(words, f.tags)) return false
  const g = (c: string) => r[SI[c]]
  // 盈亏过滤: 按当前出场规则(mode)的收益判定, 与「赚钱比例 = ret>0」同口径。
  // 盈利 = ret>0, 亏损 = ret<0, 平盘(ret=0)两边都不算; 收益留空(停牌)的记录盈亏未定, 一律排除。
  if (f.pnl) {
    const v = g(mode)
    if (v === MISSING || !isFinite(v)) return false
    const ret = v / 10000
    if (f.pnl === 1 && !(ret > 0)) return false
    if (f.pnl === 2 && !(ret < 0)) return false
  }
  // 板位: 0 = 不限(默认全量), 非 0 才按该笔自己的板位精确匹配
  if (f.bp && g('bp') !== f.bp) return false
  // 炸板过滤: 1=只看封板(触板价≈收盘价), 2=只看炸板(触板价>收盘价, 收盘未封)
  if (f.zaban && SI.zaban != null) {
    if (g('zaban') !== f.zaban - 1) return false
  }
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
  const acc = new Map<string, { n: number; nRet: number; win: number; sum: number; from: number; to: number }>()
  for (let k = 0; k < rows.length; k++) {
    const r = rows[k]
    if (!passFilter(r, filters, data.tags?.[k], mode)) continue
    const idx = r[di]
    const d = cal[idx]
    if (!d) continue
    const key = bucket === 'month' ? d.slice(0, 7) : d
    let a = acc.get(key)
    if (!a) { a = { n: 0, nRet: 0, win: 0, sum: 0, from: idx, to: idx }; acc.set(key, a) }
    else { if (idx < a.from) a.from = idx; if (idx > a.to) a.to = idx }
    // 柱高 = 这一天的全部涨停记录 (含收益留空的), 与列表条数、总账笔数一致
    a.n += 1
    const v = r[ri]
    if (v === MISSING || !isFinite(v)) continue
    // 平均收益/赚钱比例只算持有期已走完的样本
    const ret = v / 10000
    a.nRet += 1
    if (ret > 0) a.win += 1
    a.sum += ret
  }
  return [...acc.keys()].sort().map(key => {
    const a = acc.get(key)!
    return { label: key, n: a.n, win: a.nRet ? a.win / a.nRet : 0,
      mean: a.nRet ? a.sum / a.nRet : 0, from: a.from, to: a.to }
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
    // 数据本身是全量(不裁剪), 只是 1900 多根柱子一屏太密, 滑块默认停在最近约一年;
    // 拖滑块可以回到全历史任意一段, 这是可视窗口不是过滤条件。
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
          共 {num(total)} 笔 · 红柱=每天全部涨停记录数（左轴）· 黄线=平均一笔（右轴，只算收益已走完的）· 点柱子只看那天
          {bucket === 'day' && ' · 数据全量，滑块默认近一年，往前拖看更早'}
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
function toRecord(data: DabanData, row: number[], id: number, ohlc?: number[], words: number[] = NO_TAGS): ResearchRecord {
  ensureIdx(data)
  const m = data.meta
  const sym = row[SI.sym]
  const symbol = m.symbols[sym] ?? String(sym)
  const name = m.name_list[sym] ?? symbol
  const tIdx = row[SI.di]
  // 触板口径: T 日涨停(触板)当天按涨停价买入, T+1 收盘卖出。
  // startDate = 买入日(T 日), sellDate = 卖出日(T+1)。
  const startDate = m.calendar[tIdx] ?? m.start
  const buyDate = startDate
  const sellDate = m.calendar[tIdx + 1] ?? startDate
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
  const tagNames = (m.tag_defs ?? []).filter(d => hasTag(words, d.bit)).map(d => d.name)
  return {
    id: `daban-${id}`,
    symbol, name,
    startDate, endDate: sellDate,
    bars,
    raw: { row, buyDate, sellDate, tIdx, phaseName, words, tagNames },
  }
}

// ---- 分类标签勾选区 ----
const TIMING_LABEL: Record<TagDef['timing'], string> = { pre: '事前', event: '当日', buy: '买入时', review: '复盘' }
const TIMING_CLS: Record<TagDef['timing'], string> = {
  pre: 'text-bull/80', event: 'text-muted', buy: 'text-accent/80', review: 'text-bear/80',
}

/** 每个标签在「当前其它限制」下的命中笔数 (不含标签自身), 一次全量扫描出全部计数 */
function tagCounts(data: DabanData, filters: Filters, mode: string): Record<number, number> {
  ensureIdx(data)
  const defs = data.meta.tag_defs ?? []
  const out: Record<number, number> = {}
  for (const d of defs) out[d.bit] = 0
  const base = { ...filters, tags: [] }
  for (let k = 0; k < data.stats.length; k++) {
    const r = data.stats[k]
    if (!passFilter(r, base, NO_TAGS, mode)) continue
    const words = data.tags?.[k]
    for (const d of defs) if (hasTag(words, d.bit)) out[d.bit] += 1
  }
  return out
}

function TagPicker({ data, filters, mode, setExtra, onCollapse }: {
  data: DabanData
  filters: Filters
  mode: string
  setExtra: (patch: Record<string, unknown>) => void
  onCollapse: () => void
}) {
  const defs = data.meta.tag_defs ?? []
  const stale = data.meta.tag_stale ?? null
  const staleBits = useMemo(() => new Set(stale?.bits ?? []), [stale])
  const counts = useMemo(() => tagCounts(data, filters, mode), [data, filters, mode])
  const groups = useMemo(() => {
    const g: { name: string; items: TagDef[] }[] = []
    for (const d of defs) {
      let cur = g.find(x => x.name === d.group)
      if (!cur) { cur = { name: d.group, items: [] }; g.push(cur) }
      cur.items.push(d)
    }
    return g
  }, [defs])
  const selected = defs.filter(d => filters.tags.includes(d.bit))
  const toggle = (bit: number) => setExtra({
    filters: { ...filters, tags: filters.tags.includes(bit)
      ? filters.tags.filter(b => b !== bit)
      : [...filters.tags, bit] },
  })
  const clear = () => setExtra({ filters: { ...filters, tags: [] } })

  if (!defs.length) {
    return <div className="text-[11px] text-muted">分类标签还没生成，请点上方「重新计算」按当前本地日线重算。</div>
  }
  return (
    <div className="rounded-card border border-accent/30 bg-accent/[0.03] px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-accent">分类标签</span>
        <span className="text-[11px] text-muted">勾多个取交集 AND · 已选 {selected.length} 个</span>
        {selected.length > 0 && (
          <button type="button" onClick={clear}
            className="rounded-btn px-1.5 py-0.5 text-[11px] text-muted hover:bg-elevated hover:text-foreground">清空标签</button>
        )}
        <button type="button" onClick={onCollapse}
          className="rounded-btn px-1.5 py-0.5 text-[11px] text-muted hover:bg-elevated hover:text-foreground">收起 ▲</button>
        <span className="ml-auto text-[10px] text-muted/80">
          「事前」只用 T-1 及以前日线；「复盘 / 当日 / 买入时」含 T 日及以后信息，只是给已发生记录切片，不是买入信号
        </span>
      </div>
      {stale && (
        <div className="mt-2 rounded border border-warning/40 bg-warning/[0.06] px-2 py-1 text-[11px] text-warning">
          当前缓存按 <b>{stale.cached_count}</b> 条标签计算，而代码已有 <b>{stale.code_count}</b> 条：
          虚线框的 {stale.bits.length} 条（{stale.bits.join('、')} 位）<b>还没算过</b>，命中数显示为「—」不是 0。
          点上方「重新计算」即可补齐，不影响其它标签的筛选结果。
        </div>
      )}
      <div className="mt-2 grid gap-2 md:grid-cols-3">
        {groups.map(g => (
          <div key={g.name} className="rounded border border-border/60 bg-surface/60 p-2">
            <div className="mb-1.5 text-[11px] font-medium text-secondary">{g.name}</div>
            <div className="flex flex-wrap gap-1">
              {g.items.map(d => {
                const on = filters.tags.includes(d.bit)
                const isStale = staleBits.has(d.bit)
                return (
                  <button key={d.id} type="button"
                    title={isStale
                      ? `${d.rule}\n\n⚠ 当前缓存按旧标签集计算，这条还没算过，需点「重新计算」`
                      : d.rule}
                    onClick={() => toggle(d.bit)}
                    className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] transition-colors ${
                      on
                        ? 'border-accent/60 bg-accent/15 text-accent'
                        : 'border-border bg-elevated text-secondary hover:text-foreground hover:border-accent/40'
                    }${isStale ? ' border-dashed opacity-60' : ''}`}>
                    <span>{d.name}</span>
                    <span className="font-mono text-[10px] opacity-70">{isStale ? '—' : num(counts[d.bit] ?? 0)}</span>
                    <span className={`text-[9px] ${TIMING_CLS[d.timing]}`}>{TIMING_LABEL[d.timing]}</span>
                  </button>
                )
              })}
            </div>
          </div>
        ))}
      </div>
      {selected.length > 0 && (
        <div className="mt-1.5 text-[11px] text-muted">
          当前需同时满足：<b className="text-secondary">{selected.map(d => d.name).join(' + ')}</b>
        </div>
      )}
    </div>
  )
}

// ---- 顶部小部件: 总账 + 限制开关 + 一条条加限制表 ----
function ExtraPanel(props: RecipeSlotProps) {
  return <><SignalEvolution /><EventResearchPanel {...props} /></>
}

function EventResearchPanel({ ctx, setExtra }: RecipeSlotProps) {
  const dataQ = useQuery({ queryKey: ['research-workbench', 'daban', 'data'], queryFn: loadData, staleTime: Infinity })
  const data = dataQ.data
  const mode = (ctx.extra.mode as string) || 'r_t1'
  const filters = (ctx.extra.filters as Filters) || defFilters()
  // 首屏精简: 按日统计图/分类标签两块默认全部收起, 把空间直接让给 K 线列表。
  // 需要时各自一键展开, 展开状态本次会话内保持。
  const [showChart, setShowChart] = useState(false)
  const [showTags, setShowTags] = useState(false)
  const [showEvo, setShowEvo] = useState(false)
  const bucket = (ctx.extra.bucket as 'day' | 'month') || 'day'

  const stat = useMemo(() => data ? summarize(data, mode, filters) : null, [data, mode, filters])
  // 干净基线 = 只按当前板位(和图上点选的那一天)、其它不限制, 供总账「不加限制」对照卡使用。
  // 一字板不再剔除, 始终计入。
  const clean = useMemo(
    () => data ? summarize(data, mode, { ...defFilters(), bp: filters.bp, dFrom: filters.dFrom, dTo: filters.dTo }) : null,
    [data, mode, filters.bp, filters.dFrom, filters.dTo],
  )

  if (dataQ.isLoading) return <div className="border-b px-5 py-3 text-xs text-muted">正在读取打板全量数据…</div>
  if (dataQ.isError || !data) return <div className="border-b px-5 py-3 text-xs text-danger">打板数据没读到，刷新页面重试。</div>
  if (data.computed === false) return <div className="border-b px-5 py-3 text-xs text-muted">还没有计算打板数据，请点上方「重新计算」按当前本地日线重算。</div>

  const m = data.meta

  return (
    <div className="border-b border-border bg-surface/50">
      {/* 总账: 精简到 4 张卡 */}
      <div className="grid grid-cols-2 gap-2 px-5 py-2 md:grid-cols-4">
        <Card label="符合条件" value={num(stat?.n ?? 0)}
          sub={filters.pnl
            ? (filters.pnl === 1 ? '只看盈利' : '只看亏损') + ` · ${RET_SHORT[mode] ?? mode}`
            : filters.tags.length
              ? `已选 ${filters.tags.length} 标签 · 交集`
              : filters.day
                ? `只看 ${filters.day}`
                : filters.bp === 0 ? `全量 ${num(m.n_all)} 笔` : bpLabel(filters.bp)} />
        <Card label="赚钱比例" value={stat ? (stat.win * 100).toFixed(1) + '%' : '—'} sub={clean ? `不加限制 ${(clean.win * 100).toFixed(1)}%` : '收益 > 0'} tone={stat && stat.win >= 0.5 ? 'good' : 'bad'} />
        <Card label="平均一笔" value={stat ? pct(stat.mean) : '—'} sub={`中位 ${stat ? pct(stat.median) : '—'}`} tone={(stat?.mean ?? 0) >= 0 ? 'good' : 'bad'} />
        <Card label="出场规则" value={RET_SHORT[mode] ?? mode} sub={m.exit_modes[mode] ?? ''} />
      </div>

      {/* 一行口径: 全量/无抽样/含一字板/标签仅复盘切片, 防误用关键提示 */}
      <div className="border-t border-border px-5 py-1 text-[11px] text-muted">
        {filters.day && <b className="text-accent">只看 {filters.day}（点图上方「清除」取消）· </b>}
        全量 {num(m.n_all)} 笔{m.limit_basis === 'close' ? '收盘封板' : '触板（含炸板未封）'}、无抽样、含一字板；{m.limit_basis === 'close' ? '' : '买价=涨停触板价；'}标签仅复盘切片。末尾持有期不足的涨停已剔除；{!!m.n_no_ret && <>另有 {num(m.n_no_ret)} 笔因停牌收益留空。</>}
      </div>

      {/* 限制开关 */}
      <div className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-1.5">
        <span className="text-xs text-secondary">板位：</span>
        <select className="h-7 rounded-btn bg-elevated px-2 text-xs text-foreground" value={filters.bp}
          onChange={e => setExtra({ filters: { ...filters, bp: Number(e.target.value) } })}>
          <option value={0}>全部板位（{num(m.n_all)}）</option>
          {(m.pos_names ?? ['首板', '2板', '3板']).map((nm, i) => (
            <option key={i} value={i + 1}>
              {nm}（{num(m.pos_counts?.[i] ?? 0)}）
            </option>
          ))}
        </select>
        {m.limit_basis === 'touch' && <>
          <span className="text-xs text-secondary">炸板：</span>
          <select className="h-7 rounded-btn bg-elevated px-2 text-xs text-foreground" value={filters.zaban}
            onChange={e => setExtra({ filters: { ...filters, zaban: Number(e.target.value) } })}>
            <option value={0}>全部（含炸板）</option>
            <option value={1}>只看封板</option>
            <option value={2}>只看炸板</option>
          </select>
        </>}
        <span className="text-xs text-secondary">盈亏：</span>
        <select className="h-7 rounded-btn bg-elevated px-2 text-xs text-foreground" value={filters.pnl}
          title={`按当前出场规则「${RET_SHORT[mode] ?? mode}」的收益判定；平盘不算盈亏，收益留空(停牌)的记录不参与`}
          onChange={e => setExtra({ filters: { ...filters, pnl: Number(e.target.value) } })}>
          <option value={0}>全部</option>
          <option value={1}>只看盈利</option>
          <option value={2}>只看亏损</option>
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
          {Array.from({ length: Math.max(1, new Date().getFullYear() - Number(m.start.slice(0, 4)) + 1) },
            (_, i) => Number(m.start.slice(0, 4)) + i).map(y => <option key={y} value={y}>{y} 年起</option>)}
        </select>
        <button type="button" onClick={() => setExtra({ filters: defFilters() })}
          className="h-7 rounded-btn px-2 text-xs text-muted hover:bg-elevated hover:text-foreground">清空限制</button>
      </div>

      {/* 分类标签勾选: 多选取交集(AND), 与上面所有限制叠加; 命中数基于当前其它限制实时算 */}
      {showTags ? (
        <div className="px-5 pb-1.5">
          <TagPicker data={data} filters={filters} mode={mode} setExtra={setExtra} onCollapse={() => setShowTags(false)} />
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2 border-t border-border px-5 py-1">
          <button type="button" onClick={() => setShowTags(true)}
            className="shrink-0 rounded-btn px-2 py-0.5 text-[11px] text-accent hover:bg-elevated">
            分类标签（{data.meta.tag_defs?.length ?? 0}）▼
          </button>
          {filters.tags.length > 0 ? (
            <>
              <span className="text-[11px] text-muted">已选 {filters.tags.length}：</span>
              {filters.tags.map(b => {
                const d = (data.meta.tag_defs ?? []).find(x => x.bit === b)
                return <span key={b} className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">{d?.name ?? b}</span>
              })}
              <button type="button" onClick={() => setExtra({ filters: { ...filters, tags: [] } })}
                className="text-[11px] text-muted hover:text-foreground">清空</button>
            </>
          ) : (
            <span className="text-[11px] text-muted">点开按分组勾选，多选取交集</span>
          )}
        </div>
      )}

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

      {/* 标签进化: 事前形态的正向/反向组合搜索, 逐层找高胜率公式, 带进化路径树 */}
      {showEvo ? (
        <div className="border-t border-border px-3 py-2">
          <TagEvolution onCollapse={() => setShowEvo(false)} />
        </div>
      ) : (
        <div className="border-t border-border px-5 py-1">
          <button type="button" onClick={() => setShowEvo(true)}
            className="rounded-btn px-2 py-0.5 text-[11px] text-accent hover:bg-elevated">
            标签进化（组合搜索 + 进化路径）▼
          </button>
        </div>
      )}

    </div>
  )
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
    const f = (ctx.extra.filters as Filters | undefined) || defFilters()
    const nAll = page?.meta?.n_all ? num(page.meta.n_all as number) : '全量'
    const scope = f.bp === 0 ? '全板位' : bpLabel(f.bp)
    return `${scope}全部 ${nAll} 笔涨停 · 当天涨停价买入(炸板按触板价)、按「${RET_SHORT[mode]}」卖 · 标签仅事后复盘切片，非买入信号。`
  },

  sortOptions: [
    { value: 'date', label: '涨停日期' },
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
    // 后端还没重算过: 交给工作台显示「还没有计算，请点重新计算」空态。
    if (data.computed === false) {
      return { rows: [], total: 0, computed: false, meta: { n_all: 0 } }
    }
    ensureIdx(data)
    const filters = (ctx.extra.filters as Filters) || defFilters()
    const mode = (ctx.extra.mode as string) || 'r_t1'
    const ri = SI[mode]

    // 直接过滤全量交易, 不是抽样池 —— 点某一天, 那天的每一笔都在, 一条不少。
    const ids: number[] = []
    for (let i = 0; i < data.stats.length; i++) {
      const row = data.stats[i]
      if (!passFilter(row, filters, data.tags?.[i], mode)) continue
      const sym = data.meta.symbols[row[SI.sym]] ?? ''
      const name = data.meta.name_list[row[SI.sym]] ?? ''
      if (ctx.q && !(sym.includes(ctx.q) || name.includes(ctx.q))) continue
      const d = data.meta.calendar[row[SI.di]] ?? ''
      if (ctx.start && d < ctx.start) continue
      if (ctx.end && d > ctx.end) continue
      ids.push(i)
    }
    const isNoRet = (i: number) => {
      const v = data.stats[i][ri]
      return ctx.sort === 'ret' && (v === MISSING || !isFinite(v))
    }
    const val = (i: number) => {
      const row = data.stats[i]
      if (ctx.sort === 'date') return row[SI.di]
      if (ctx.sort === 'boards') return row[SI.boards]
      if (ctx.sort === 'lu') return row[SI.lu] ?? 0
      return row[ri]                          // 'ret' 按当前出场收益
    }
    // 收益未走完的记录按收益排序时一律沉底, 不拿 -9999 当极端亏损混进来
    ids.sort((a, b) => {
      const na = isNoRet(a), nb = isNoRet(b)
      if (na !== nb) return na ? 1 : -1
      return ctx.order === 'desc' ? val(b) - val(a) : val(a) - val(b)
    })
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
      rows: page.map((i, k) => toRecord(data, data.stats[i], i, ohlcRows[k], data.tags?.[i])),
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
    const { row, buyDate, phaseName, tagNames } = rec.raw as { row: number[]; buyDate: string; phaseName: string; tagNames?: string[] }
    const raw = row[SI[mode]]
    const noRet = raw === MISSING || !isFinite(raw)
    const ret = noRet ? 0 : raw / 10000
    const boards = row[SI.boards]
    const lu = row[SI.lu]
    const yizi = row[SI.yizi_t] === 1 || row[SI.yizi_b] === 1
    // 全量视图里各档板位混在一起, 标签取这笔交易自己的板位, 而不是当前筛选值
    const label = bpLabel(row[SI.bp])
    return (
      <>
        <div className="flex items-baseline justify-between gap-2">
          <div className="min-w-0 truncate text-sm font-medium text-foreground">{rec.name || rec.symbol}</div>
          {noRet
            ? <div className="shrink-0 text-xs font-medium text-muted">收益留空(停牌)</div>
            : <div className={`shrink-0 text-sm font-semibold ${ret >= 0 ? 'text-bull' : 'text-bear'}`}>{pct(ret)}</div>}
        </div>
        <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-muted">
          <span className="truncate font-mono">{rec.symbol}</span>
          <span>{label} {rec.startDate}</span>
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted">
          <span>{buyDate} 当日涨停价买入</span>
          <span>· 涨停 {lu} 家</span>
          <span>· {phaseName}</span>
          <span title="最终连板数是买完之后才走出来的，不能拿来当买入条件">· 后来 <b className="text-secondary">{boards} 连板</b>（事后，不能选股）</span>
          {yizi && <span className="text-bear">· 一字买不进</span>}
        </div>
        {!!tagNames?.length && (
          <div className="mt-1 flex flex-wrap gap-0.5">
            {tagNames.slice(0, 3).map(n => (
              <span key={n} className="inline-block rounded bg-accent/10 px-1 py-px text-[10px] leading-tight text-accent">{n}</span>
            ))}
            {tagNames.length > 3 && <span className="text-[10px] text-muted">+{tagNames.length - 3}</span>}
          </div>
        )}
      </>
    )
  },

  mini: (rec) => {
    const { buyDate, sellDate } = rec.raw as { buyDate: string; sellDate: string }
    return {
      band: { start: buyDate, end: sellDate },
      shadeAfter: sellDate, shadeAfterInclusive: true,
      vlines: [
        { date: buyDate, color: '#7AA5F0', dash: '2 2' },   // T 日涨停价买入
        { date: sellDate, color: '#94A3B8', dash: '2 2' },  // T+1 收盘卖出
      ],
    } as MiniSpec
  },

  dialogDecor: (rec, ctx, frame): DialogDecor => {
    const mode = (ctx.extra.mode as string) || 'r_t1'
    const { row, buyDate, sellDate } = rec.raw as { row: number[]; buyDate: string; sellDate: string }
    const raw = row[SI[mode]]
    const noRet = raw === MISSING || !isFinite(raw)
    const ret = noRet ? 0 : raw / 10000
    const boards = row[SI.boards]
    const label = bpLabel(row[SI.bp])
    const retText = noRet ? `「${RET_SHORT[mode]}」收益留空（持有期内停牌/无价）` : `按「${RET_SHORT[mode]}」${pct(ret)}`
    return {
      subtitle: `${label} ${buyDate} 涨停价买入 · ${sellDate} 收盘卖 · ${retText} · 后来走出 ${boards} 连板（事后）`,
      ranges: [{ start: buyDate, end: sellDate, label: `${label}持有`, color: 'rgba(226,177,90,0.22)' }],
      markers: [
        { date: buyDate, kind: 'buy', above: true, label: '涨停价买入', color: '#E2B15A' },
        { date: sellDate, kind: 'sell', above: false, label: '收盘卖', color: '#7AA5F0' },
      ],
      focus: frame === 'year' ? 'all' : { start: buyDate, end: sellDate, padding: 12 },
      chartKey: frame,
    }
  },

  frames: {
    spanLabel: '看这段', allLabel: '看全年',
    spanText: '当前对准涨停价买入到次日收盘卖出这一段。', allText: '当前是买入前后各一年，橙色是涨停价买入那天。',
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
    if (f.tags.length) {
      return { title: '没有同时满足这些标签的涨停记录', hint: '分类标签是取交集(AND)，勾得越多剩得越少。点上面「清空标签」或取消几个再试；也可以放宽板位/年份/情绪限制。' }
    }
    return { title: '这个条件下没有交易', hint: '放宽限制条件、清空年份或搜索词再试。列表就是全量交易本身，没有抽样。' }
  },
  notComputed: {
    title: '还没有计算，请点重新计算',
    hint: '按当前本地日线重算 2016-11-10 起全部板位的全部涨停记录（约半分钟）。算完会保存下来，之后打开页面直接读取，不会因为行情更新自动重算。',
  },
  rebuild: {
    label: '重新计算',
    pendingLabel: '正在重算全量交易…',
    run: async () => {
      await api.dabanResearchRebuild()
      refreshDabanData()
    },
  },
  invalidateKeys: [['research-workbench', 'daban', 'data']],
  pageSize: 12,
}
