import type { ResearchRecipe } from '../shared/types'
import { limitUpRecipe } from './limitUp'
import { riseRecipe } from './rise'
import { strategyRecipe } from './strategy'
import { dabanRecipe } from './daban'

/**
 * 研究说明卡注册表。
 *
 * 新增一种研究的完整步骤:
 *   1. 在 recipes/ 下新建一张卡, 实现 ResearchRecipe 接口(见 shared/types.ts)
 *   2. 把卡加进下面这个数组
 * 壳、放大框、缩略 K 线、分页、重建流程都不用改。
 * 数组顺序 = 页面顶部标签顺序; 第一项是默认标签。
 */
export const RECIPES: ResearchRecipe[] = [
  limitUpRecipe,
  riseRecipe,
  strategyRecipe,
  dabanRecipe,
]

/** 旧地址 → 对应标签, 供跳转使用 */
export const LEGACY_TABS: Record<string, string> = {
  'limit-up-research': limitUpRecipe.id,
  'rise-research': riseRecipe.id,
  'strategy-review': strategyRecipe.id,
}
