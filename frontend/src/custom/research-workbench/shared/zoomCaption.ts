import type { ResearchBar } from './types'

export interface ZoomCaptionArgs {
  bars: ResearchBar[]
  fromDate: string
  toDate: string
  asOf?: string
  barsBefore: number
  barsAfter: number
  frame: string
  nouns: { beforeWindow: string; spanNoun: string; afterWindow: string }
  frames?: { spanLabel: string; allLabel: string; spanText: string; allText: string }
}

/**
 * 放大图说明行。只此一份, 三种研究共用。
 *
 * 回归要点: from_date / to_date 是后端「理论上的一年窗口」, 本地日线可能没存那么早 / 那么晚。
 * 标题必须写 bars 的真实首末根, 不能写理论窗口起点(那会显示图上并不存在的日期)。
 * 不足的部分另外明确说出来, 前后对称。
 */
export function buildZoomCaption(a: ZoomCaptionArgs): string {
  const firstBar = a.bars[0]?.date ?? a.fromDate
  const lastBar = a.bars[a.bars.length - 1]?.date ?? a.toDate
  const hasSides = a.barsBefore > 0 || a.barsAfter > 0
  const sides = hasSides ? `，前 ${a.barsBefore} 根，后 ${a.barsAfter} 根` : ''

  let frameText = ''
  if (a.frames) frameText = a.frame === 'year' ? a.frames.allText : a.frames.spanText
  else if (a.nouns.spanNoun) frameText = a.nouns.spanNoun

  const shortBefore = a.bars.length > 0 && a.fromDate && firstBar > a.fromDate
  const shortAfter = Boolean(a.asOf && a.toDate > a.asOf && lastBar < a.toDate)
  const beforeNote = shortBefore ? `${a.nouns.beforeWindow}要 ${a.fromDate}，本地日线只到 ${firstBar}。` : ''
  const afterNote = shortAfter ? `${a.nouns.afterWindow}要到 ${a.toDate}，最新行情只到 ${a.asOf}。` : ''

  return `放大图 ${firstBar} ~ ${lastBar}${sides}。${frameText}${beforeNote}${afterNote}`
}
