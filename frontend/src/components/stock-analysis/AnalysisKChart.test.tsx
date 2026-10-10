// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AnalysisKChart, type PriceLevel, type LevelType } from './AnalysisKChart'
import type { DpStructure, KlineRow, WaveStructure } from '@/lib/api'

const chart = vi.hoisted(() => ({
  option: {} as any,
  setOption: vi.fn((option: any, _notMerge?: boolean) => { chart.option = option }),
  getOption: vi.fn(() => chart.option),
  on: vi.fn(), resize: vi.fn(), dispose: vi.fn(),
}))
vi.mock('echarts', () => ({ init: () => chart }))

let host: HTMLDivElement
let root: Root
const rows = Array.from({ length: 160 }, (_, index) => ({
  date: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10),
  open: 100, close: 101, low: 99, high: 102, volume: 10,
})) as KlineRow[]
const levels = { pivot: [{ value: 100, type: 'pivot', label: 'P', side: 'neutral' }] } as Record<LevelType, PriceLevel[]>
const dpStructure: DpStructure = Object.fromEntries(['L0', 'L1', 'L2', 'L3'].map(level => [level, {
  level, epsilon: 1, turning_points: [], segments: [], current_tail: null,
}]))

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  chart.option = {}
  chart.setOption.mockClear()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
})
afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})
async function render(viewportKey = '600000.SH:250', data = rows) {
  await act(async () => root.render(
    <AnalysisKChart rows={data} levels={levels} dpStructure={dpStructure} viewportKey={viewportKey} />,
  ))
}
function zoom() {
  chart.option.dataZoom.forEach((item: any) => Object.assign(item, { start: 10, end: 35 }))
}
function expectZoom(start = 10, end = 35) {
  expect(chart.option.dataZoom.map((item: any) => [item.start, item.end])).toEqual([[start, end], [start, end]])
}
it('preserves the user viewport across DP and price-level switches and data refreshes', async () => {
  await render()
  zoom()
  for (const label of ['L0', 'L1', '枢轴点']) {
    const button = [...host.querySelectorAll('button')].find(item => item.textContent?.includes(label))!
    expect(button).toBeDefined()
    await act(async () => button.click())
    expectZoom()
  }
  await render('600000.SH:250', rows.map(row => ({ ...row, close: 103 })))
  expectZoom()
  // Still replace the full option: removed overlays must not survive via merging.
  expect(chart.setOption.mock.calls.at(-1)?.[1]).toBe(true)
})
it('resets the viewport for a new stock, requested range, or changed date axis', async () => {
  await render()
  expectZoom(25, 100)
  zoom()
  await render('600001.SH:250')
  expectZoom(25, 100)
  zoom()
  await render('600001.SH:500')
  expectZoom(25, 100)
  zoom()
  await render('600001.SH:500', rows.slice(0, 100))
  expectZoom(0, 100)
})

it('renders new L0/L1 waves, hides higher levels, and preserves viewport', async () => {
  const waveStructure: WaveStructure = {
    L0: { level: 'L0', algorithm: 'market_structure', turning_points: [], segments: [], current_tail: null, segment_count: 0 },
    L1: { level: 'L1', algorithm: 'market_structure', turning_points: [], segments: [], segment_count: 1,
      current_tail: { state: 'UP', start_date: rows[0].date, end_date: rows[10].date,
        start_price: 100, end_price: 120, duration: 11, net_return: 0.2, amplitude: 0.2,
        confirmed_date: null, reversal_threshold: 5 } },
    L2: { level: 'L2', algorithm: 'market_structure', turning_points: [], segments: [], segment_count: 1,
      current_tail: { state: 'DOWN', start_date: rows[10].date, end_date: rows[20].date,
        start_price: 120, end_price: 100, duration: 11, net_return: -0.16, amplitude: 0.16,
        confirmed_date: null, reversal_threshold: 5 } },
  }
  await act(async () => root.render(<AnalysisKChart rows={rows} levels={levels} waveStructure={waveStructure} />))
  expect(host.textContent).toContain('分级波段')
  expect(host.textContent).toContain('L2')
  expect(host.textContent).not.toContain('L3')
  expect(chart.option.series.find((item: any) => item.name === 'L1 UP 当前尾段').lineStyle.type).toBe('dashed')
  expect(host.textContent).toContain('L2')
  expect(chart.option.series.some((item: any) => item.name === 'L2 DOWN 当前尾段')).toBe(false)
  zoom()
  const button = [...host.querySelectorAll('button')].find(item => item.textContent?.includes('L1'))!
  await act(async () => button.click())
  expectZoom()
  expect(chart.option.series.some((item: any) => item.name === 'L1 UP 当前尾段')).toBe(false)
})

it('plots weekly/monthly structure on its candle axis without daily waves and preserves overlay zoom', async () => {
  const structure = {
    trend: 'bearish' as const, trend_label: '空头结构', last_high: 120, last_low: 90,
    confirmation_bars: 3, events: [], cluster_levels: [], support_zones: [], resistance_zones: [],
    swing_points: [
      { date: rows[10].date, price: 120, type: 'high' as const, confirmed_date: rows[13].date },
      { date: rows[20].date, price: 90, type: 'low' as const, confirmed_date: rows[23].date },
    ],
  }
  for (const timeframe of ['W', 'M'] as const) {
    await act(async () => root.render(<AnalysisKChart rows={rows} levels={{} as Record<LevelType, PriceLevel[]>}
      structure={structure} timeframe={timeframe} dpStructure={dpStructure} />))
    expectZoom(25, 100)
    expect(host.textContent).toContain(timeframe === 'W' ? '周线结构折线' : '月线结构折线')
    expect(host.textContent).not.toContain('分级波段')
    expect(host.textContent).not.toContain('枢轴点')
    expect(chart.option.series.find((item: any) => item.name === '结构回撤段').data)
      .toEqual([[rows[10].date, 120], [rows[20].date, 90]])
    zoom()
    const button = [...host.querySelectorAll('button')].find(item => item.textContent?.includes('结构折线'))!
    await act(async () => button.click())
    expectZoom()
    expect(chart.option.series.some((item: any) => item.name === '结构回撤段')).toBe(false)
    await act(async () => button.click())
  }
})
