// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AnalysisKChart, type PriceLevel, type LevelType } from './AnalysisKChart'
import type { DpStructure, KlineRow } from '@/lib/api'

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
  for (const label of ['L0', 'L1', 'L2', 'L3', '枢轴点']) {
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
