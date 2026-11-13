import type { ComponentType, ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import type { ChartMarker, ChartRange } from '@/lib/chart-types'

/**
 * 「研究说明卡」契约 —— 研究工作台的全部差异都写在这里。
 *
 * 设计约束(改动前先读):
 *  1. 壳(Workbench.tsx) 不得出现针对某个具体研究的判断分支。
 *     差异一律由说明卡声明: 字段映射 / 文案 / 筛选组件 / 小部件 / 装饰。
 *  2. 新增一种研究 = 新增一张说明卡 + 在 recipes/index.ts 注册, 不改壳。
 *  3. 后端三套接口保持原样, 字段归一化由各自的 fetchPage 完成。
 */

/** 统一的 K 线柱(三套后端字段名一致, 仅 volume 可选差异) */
export interface ResearchBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

/** 统一记录: 任何研究的一条结果都必须归一成这 6 个字段 */
export interface ResearchRecord {
  id: string
  symbol: string
  name: string
  /** 放大图高亮段的起止(策略信号为同一天) */
  startDate: string
  endDate: string
  bars: ResearchBar[]
  /** 原始行, 供说明卡渲染自己特有的数字 */
  raw: Record<string, unknown>
}

/** 统一列表结果 */
export interface ResearchPage {
  rows: ResearchRecord[]
  total: number
  computed: boolean
  asOf?: string
  barsBefore?: number
  barsAfter?: number
  /** 副标题/统计卡用的元信息 */
  meta?: Record<string, unknown>
}

/** 统一放大图结果 */
export interface ResearchZoom {
  bars: ResearchBar[]
  fromDate: string
  toDate: string
  barsBefore: number
  barsAfter: number
}

/** 壳持有的通用查询上下文(说明卡只读) */
export interface ResearchCtx {
  start: string
  end: string
  q: string
  sort: string
  order: 'asc' | 'desc'
  offset: number
  limit: number
  /** 说明卡私有状态, 壳不解释其含义 */
  extra: Record<string, unknown>
}

/** 缩略 K 线的装饰(壳统一画蜡烛, 说明卡只给高亮配置) */
export interface MiniSpec {
  band?: { start: string; end: string }
  /** 从哪一天起把后面染成「事后」色 */
  shadeAfter?: string
  /** true = 含当天(信号日); false/不填 = 严格在之后(涨停段/连涨段的结束日) */
  shadeAfterInclusive?: boolean
  vlines?: { date: string; color: string; dash?: string }[]
}

/** 放大框的装饰(壳统一画图表, 说明卡只给这段该标什么) */
export interface DialogDecor {
  /** 弹窗标题下的说明行 */
  subtitle: string
  ranges: ChartRange[]
  markers: ChartMarker[]
  focus?: { start: string; end: string; padding?: number } | 'all'
  /** 图表 key 后缀, 用于切换帧时强制重绘 */
  chartKey?: string
}

/** 说明卡插槽组件的公开能力 */
export interface RecipeSlotProps {
  ctx: ResearchCtx
  /** 当前列表结果(小部件如统计卡要用); 未加载完时为 undefined */
  page?: ResearchPage
  /** 合并写入说明卡私有状态 */
  setExtra: (patch: Record<string, unknown>) => void
  /** 让壳重新拉列表 */
  refresh: () => void
  rebuilding: boolean
  /** 该研究的查询键前缀, 供小部件挂自己的缓存 */
  keyPrefix: readonly unknown[]
}

/** 重建任务的统一形状 */
export interface ResearchJobView {
  done: boolean
  failed: boolean
  /** 进度区要显示的内容(说明卡自己渲染) */
  node: ReactNode
}

export interface ResearchRecipe {
  id: string
  label: string
  icon: LucideIcon
  /** 顶部副标题, 可随查询结果变化 */
  subtitle: (page: ResearchPage | undefined, ctx: ResearchCtx) => string
  sortOptions: { value: string; label: string }[]
  defaultSort: string
  /** 私有状态初值(如最少连板、达标天数、持有日) */
  initialExtra: Record<string, unknown>
  /** 只返回「参与列表查询」的键; 纯 UI 状态(面板展开/图表分桶)不返回, 避免无谓重查 */
  queryExtra: (extra: Record<string, unknown>) => Record<string, unknown>
  /** 拉列表, 完成字段归一化 */
  fetchPage: (ctx: ResearchCtx) => Promise<ResearchPage>
  /** 拉放大图, 完成字段归一化 */
  fetchZoom: (rec: ResearchRecord) => Promise<ResearchZoom>
  /** 顶部工具栏里该研究特有的筛选控件 */
  ExtraFilters?: ComponentType<RecipeSlotProps>
  /** 列表上方的小部件(统计卡 / 起涨分布图 / 进度条 / 第二段筛选) */
  ExtraPanel?: ComponentType<RecipeSlotProps>
  /** 卡片里的文字部分; 缩略 K 线由壳统一画 */
  card: (rec: ResearchRecord, ctx: ResearchCtx) => ReactNode
  mini: (rec: ResearchRecord, ctx: ResearchCtx) => MiniSpec
  /** 放大框装饰; frame 为 'span' | 'year' */
  dialogDecor: (rec: ResearchRecord, ctx: ResearchCtx, frame: string) => DialogDecor
  /** 是否提供「看这段 / 看全年」切换; 不提供则只有单帧 */
  frames?: { spanLabel: string; allLabel: string; spanText: string; allText: string }
  /** 放大图说明文案里那几个专有名词 */
  zoomNouns: { beforeWindow: string; spanNoun: string; afterWindow: string }
  /** 空态文案 */
  empty: (ctx: ResearchCtx) => { title: string; hint: string }
  /** 尚未计算时的空态(不提供则用通用文案) */
  notComputed?: { title: string; hint: string }
  /** 重建(重新计算 / 运行选股) */
  rebuild?: {
    label: string
    pendingLabel: string
    canRun?: (ctx: ResearchCtx) => boolean
    run: (ctx: ResearchCtx) => Promise<{ jobId?: string } | void>
  }
  /** 异步任务轮询(算得慢的研究才有) */
  job?: {
    poll: (jobId: string) => Promise<ResearchJobView>
  }
  /** 重建后额外要失效的查询键(其它仍读同一份后端产物的页面) */
  invalidateKeys?: readonly (readonly unknown[])[]
  pageSize?: number
}
