/**
 * 侧栏默认排序基准 — 扩展域页面迁移 (custom/) 的老用户升级兼容。
 *
 * 迁移前这 19 个路径按此顺序构成内置侧栏 (Layout 核心导航数组)。
 * 页面迁往 custom/<ns>/extension.tsx 后, 无保存排序的环境 (新装或
 * 从未调整过菜单的用户) 若按「核心在前、扩展追加」拼接, 11 个页面会
 * 整体沉底, 老用户升级即感知错位。因此默认顺序统一按本基准排序,
 * 升级前后侧栏一致; 已保存过 nav_order 的用户按保存值展示, 不受影响。
 *
 * 未知路径 (声明式分析页、二开扩展) 排在已知路径之后, 保持相对顺序。
 * 修改本清单需同步回归 navOrder.test.ts。
 */
export const DEFAULT_NAV_PATHS = [
  '/', '/watchlist', '/screener', '/factors', '/backtest',
  '/stock-analysis', '/limit-ladder', '/concept-analysis', '/industry-analysis', '/financials',
  '/monitor', '/regime', '/abnormal', '/lots', '/paper', '/signals', '/review',
  '/indices', '/data',
] as const

const INDEX = new Map<string, number>(DEFAULT_NAV_PATHS.map((p, i) => [p, i]))

/** 路径的默认序号; 未知路径返回 Infinity (排在全部已知路径之后) */
export function defaultNavIndex(path: string): number {
  return INDEX.get(path) ?? Number.POSITIVE_INFINITY
}

/**
 * 按默认基准排序的比较器。两个未知路径相减会产生 NaN 破坏排序,
 * 必须先判等再相减 (相等时依赖 sort 的稳定性保持相对顺序)。
 */
export function compareByDefaultNav(a: string, b: string): number {
  const ia = defaultNavIndex(a)
  const ib = defaultNavIndex(b)
  if (ia === ib) return 0
  return ia - ib
}

/**
 * 扩展域页面路径 (迁移到 custom/ 的 11 个官方页面)。
 * 新装用户的一次性种子会把它们写入 nav_hidden 默认隐藏;
 * 老用户 (升级前已完成 onboarding) 不受影响。用户随时可在
 * 设置 → 菜单设置 里点眼睛图标逐个开启。
 */
export const EXT_DOMAIN_PAGE_PATHS: readonly string[] = [
  '/stock-analysis', '/limit-ladder', '/concept-analysis', '/industry-analysis', '/financials',
  '/monitor', '/abnormal', '/lots', '/paper', '/signals', '/review',
]

/**
 * 新装用户种子: 返回应写入 nav_hidden 的完整列表 (与已有隐藏项取并集);
 * 老用户返回 null 表示不动。判定信号是 onboarding_completed —— 首启
 * 向导强制完成, 升级前用过应用的用户必然为 true。
 */
export function seedHiddenForNewUser(
  onboardingCompleted: boolean | undefined,
  currentHidden: readonly string[] | null | undefined,
): string[] | null {
  if (onboardingCompleted !== false) return null
  const hidden = new Set(currentHidden ?? [])
  for (const p of EXT_DOMAIN_PAGE_PATHS) hidden.add(p)
  return [...hidden]
}
/** 侧栏菜单顺序里，概念分析 / 行业分析合并为板块分析后的 id 迁移。 */

export const SECTOR_ANALYSIS_PATH = '/sector-analysis'

export const LEGACY_SECTOR_ANALYSIS_PATHS = ['/concept-analysis', '/industry-analysis'] as const

const LEGACY_SECTOR_IDS = new Set<string>(LEGACY_SECTOR_ANALYSIS_PATHS)

function isSectorNavId(id: string): boolean {
  return id === SECTOR_ANALYSIS_PATH || LEGACY_SECTOR_IDS.has(id)
}

/**
 * 把已保存的菜单顺序里的旧页面 id 收成一个「板块分析」。
 * 两个旧 id 都在时保留先出现的位置，丢掉后一个，避免合并后多出一条或跳回默认位。
 */
export function migrateNavOrder(ids: string[]): string[] {
  let placed = false
  const out: string[] = []
  for (const id of ids) {
    if (!isSectorNavId(id)) {
      out.push(id)
      continue
    }
    if (placed) continue
    out.push(SECTOR_ANALYSIS_PATH)
    placed = true
  }
  return out
}

/**
 * 隐藏列表同步收成新 id。
 * 只隐藏了概念或只隐藏了行业时，合并页仍显示——另一页还在用。
 * 两个旧页都隐藏，或已经隐藏了新 id，合并页才隐藏。
 */
export function migrateNavHidden(hidden: string[]): string[] {
  const hiddenSet = new Set(hidden)
  const bothLegacyHidden = LEGACY_SECTOR_ANALYSIS_PATHS.every(id => hiddenSet.has(id))
  const hideSector = hiddenSet.has(SECTOR_ANALYSIS_PATH) || bothLegacyHidden
  let placed = false
  const out: string[] = []
  for (const id of hidden) {
    if (!isSectorNavId(id)) {
      out.push(id)
      continue
    }
    if (!hideSector || placed) continue
    out.push(SECTOR_ANALYSIS_PATH)
    placed = true
  }
  return out
}
