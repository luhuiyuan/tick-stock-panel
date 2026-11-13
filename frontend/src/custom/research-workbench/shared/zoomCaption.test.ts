// 回归(原 limit-up-research / rise-research 两份测试合并而来):
// 放大图标题曾把后端的 from_date(理论上的一年窗口起点)当成图上首根日期。
// 本地只存最近一年日线时, from_date 会早于实际首根, 标题会显示图上并不存在的日期。
// 现在三种研究共用一份说明行, 前后不足一年的提示必须对称。
import { expect, it } from 'vitest'
import { buildZoomCaption } from './zoomCaption'

const bars = ['2025-11-03', '2026-04-10', '2026-05-12', '2026-05-15', '2026-10-30'].map(date => ({
  date, open: 10, high: 11, low: 9, close: 10.5, volume: 1000,
}))

const base = {
  bars,
  barsBefore: 124,
  barsAfter: 113,
  asOf: '2026-10-30',
  frame: 'span',
}

it('涨停段: 标题写实际首根, 不写图上不存在的理论窗口起点', () => {
  const text = buildZoomCaption({
    ...base,
    fromDate: '2025-05-12',
    toDate: '2027-05-15',
    nouns: { beforeWindow: '首板前一年', spanNoun: '', afterWindow: '一年' },
    frames: { spanLabel: '看连板', allLabel: '看全年', spanText: '当前对准这段涨停。', allText: '当前是全年，红色区间是这段涨停。' },
  })
  expect(text).toContain('放大图 2025-11-03 ~ 2026-10-30')
  expect(text).not.toContain('放大图 2025-05-12')
  expect(text).toContain('首板前一年要 2025-05-12，本地日线只到 2025-11-03')
  expect(text).toContain('一年要到 2027-05-15，最新行情只到 2026-10-30')
})

it('连涨段: 前后不足一年的提示对称, 用「起涨前一年」而非「首板前一年」', () => {
  const text = buildZoomCaption({
    ...base,
    fromDate: '2025-10-22',
    toDate: '2027-10-30',
    nouns: { beforeWindow: '起涨前一年', spanNoun: '', afterWindow: '一年' },
    frames: { spanLabel: '看这段', allLabel: '看全年', spanText: '当前对准这段上涨。', allText: '当前是全年，红色区间是这段上涨。' },
  })
  expect(text).toContain('放大图 2025-11-03 ~ 2026-10-30')
  expect(text).not.toContain('放大图 2025-10-22')
  expect(text).toContain('起涨前一年要 2025-10-22，本地日线只到 2025-11-03')
  expect(text).toContain('一年要到 2027-10-30，最新行情只到 2026-10-30')
})

it('看全年帧切换时说明行跟着变', () => {
  const args = {
    ...base,
    fromDate: '2025-05-12',
    toDate: '2027-05-15',
    frame: 'year',
    nouns: { beforeWindow: '首板前一年', spanNoun: '', afterWindow: '一年' },
    frames: { spanLabel: '看连板', allLabel: '看全年', spanText: '当前对准这段涨停。', allText: '当前是全年，红色区间是这段涨停。' },
  }
  expect(buildZoomCaption(args)).toContain('当前是全年，红色区间是这段涨停。')
  expect(buildZoomCaption({ ...args, frame: 'span' })).toContain('当前对准这段涨停。')
})

it('策略信号(没有看全年切换): 用说明卡自己的名词, 不冒充涨停/连涨', () => {
  const text = buildZoomCaption({
    ...base,
    barsBefore: 0,
    barsAfter: 0,
    fromDate: '2025-05-12',
    toDate: '2027-05-15',
    nouns: { beforeWindow: '信号前一年', spanNoun: '当前对准买入到卖出。', afterWindow: '一年' },
  })
  expect(text).not.toContain('，前 0 根，后 0 根')
  expect(text).toContain('当前对准买入到卖出。')
  expect(text).toContain('信号前一年要 2025-05-12，本地日线只到 2025-11-03')
})
