// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { StockAnalysisBoard } from './StockAnalysis'

const fixtures = vi.hoisted(() => ({
  kline: vi.fn(), levels: vi.fn(), props: {} as any,
}))
vi.mock('@/lib/api', () => ({ api: {
  klineDaily: fixtures.kline, stockAnalysisLevels: fixtures.levels,
} }))
vi.mock('@/components/stock-analysis/AnalysisKChart', () => ({
  AnalysisKChart: (props: any) => {
    fixtures.props = props
    return <div data-chart>{props.timeframe}:{props.rows[0].date}</div>
  },
}))

let host: HTMLDivElement
let root: Root
let client: QueryClient
const candle = { date: '2026-09-30', open: 10, high: 12, low: 9, close: 11, volume: 100 }
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixtures.kline.mockReset().mockResolvedValue({ rows: [{ ...candle, date: '2026-10-08' }] })
  fixtures.levels.mockReset().mockImplementation(async (_symbol, _days, _range, timeframe) => ({
    rows: timeframe === 'D' ? undefined : [candle],
    levels: {}, close: 11, structure: { trend: 'unknown' },
    wave_structure: timeframe === 'D' ? { L0: {}, L1: {} } : undefined,
  }))
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
})
afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
})
async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}
async function switchTo(label: string) {
  const button = [...host.querySelectorAll('button')].find(item => item.textContent === label)!
  await act(async () => button.click())
  await settle()
}
it('switches period data, uses longer default ranges and returns to cached daily L0/L1', async () => {
  await act(async () => root.render(<QueryClientProvider client={client}>
    <StockAnalysisBoard symbol="603583.SH" />
  </QueryClientProvider>))
  await settle()
  expect(host.querySelector('[data-chart]')?.textContent).toBe('D:2026-10-08')
  for (const [label, timeframe, days] of [['周线', 'W', 1826], ['月线', 'M', 3652]] as const) {
    await switchTo(label)
    const call = fixtures.levels.mock.calls.at(-1)!
    expect(call[3]).toBe(timeframe)
    expect((Date.parse(call[2].end) - Date.parse(call[2].start)) / 86400000).toBe(days)
    expect(host.querySelector('[data-chart]')?.textContent).toBe(`${timeframe}:2026-09-30`)
    expect(fixtures.props.waveStructure).toBeUndefined()
    expect(fixtures.props.viewportKey).toContain(`:${timeframe}:`)
    expect(host.textContent).toContain('首尾可能不完整的周期暂不展示')
    expect(fixtures.kline).toHaveBeenCalledTimes(1)
  }
  await switchTo('日线')
  expect(host.querySelector('[data-chart]')?.textContent).toBe('D:2026-10-08')
  expect(fixtures.props.waveStructure).toBeDefined()
})

it('supports custom period dates and shows empty/error states without daily candles', async () => {
  await act(async () => root.render(<QueryClientProvider client={client}>
    <StockAnalysisBoard symbol="603583.SH" />
  </QueryClientProvider>))
  await settle()
  await switchTo('周线')
  const select = host.querySelector('select')!
  await act(async () => {
    select.value = 'custom'
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await settle()
  expect(host.querySelector('[data-chart]')).toBeNull()
  const start = host.querySelector<HTMLInputElement>('input[aria-label="自定义开始日期"]')!
  const end = host.querySelector<HTMLInputElement>('input[aria-label="自定义结束日期"]')!
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setValue.call(end, '2025-12-31')
    end.dispatchEvent(new Event('change', { bubbles: true }))
    setValue.call(start, '2025-01-01')
    start.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await settle()
  expect(fixtures.levels.mock.calls.at(-1)?.[2]).toEqual({ start: '2025-01-01', end: '2025-12-31' })
  fixtures.levels.mockResolvedValueOnce({ rows: [], levels: {}, close: null })
  await switchTo('月线')
  expect(host.textContent).toContain('暂无月线数据')
  expect(host.querySelector('[data-chart]')).toBeNull()
  fixtures.levels.mockRejectedValueOnce(new Error('network failed'))
  await act(async () => { void client.invalidateQueries() })
  await settle()
  expect(host.textContent).toContain('月线数据加载失败')
  expect(host.querySelector('[data-chart]')).toBeNull()
})
