// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { KlineRow } from '@/lib/api'

const state = vi.hoisted(() => ({
  rows: [] as KlineRow[],
  chip: undefined as { width?: number } | null | undefined,
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({
    data: { rows: state.rows, stock_info: { name: '西部矿业' } },
    isLoading: false,
    isError: false,
  }),
}))

vi.mock('@/components/EChartsCandlestick', () => ({
  EChartsCandlestick: ({
    chip,
  }: {
    chip?: { width?: number } | null
  }) => {
    state.chip = chip
    return (
      <div data-testid="marks">
      </div>
    )
  },
  OVERLAY_INDICATORS: [],
  SUB_CHARTS: [],
}))

import { StockDailyKChart } from './StockDailyKChart'

function bars(count: number): KlineRow[] {
  return Array.from({ length: count }, (_, index) => {
    const date = new Date(Date.UTC(2026, 7, 10 + index)).toISOString().slice(0, 10)
    return { date, open: 10, high: 11, low: 9, close: 10.4, volume: 1000 }
  })
}

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  state.rows = bars(16)
  localStorage.clear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})


async function renderChart() {
  await act(async () => root.render(<StockDailyKChart symbol="601168.SH" showMA={false} />))
}

it('筹码默认打开，关掉后不再占右侧宽度', async () => {
  await renderChart()
  const button = [...host.querySelectorAll('button')].find(item => item.textContent === '筹码')
  if (!button) throw new Error('找不到筹码按钮')
  expect(button.className).toContain('bg-[#C74040]/10')
  expect(button.title).toContain('本地计算（东财算法）')
  expect(state.chip?.width).toBe(72)

  await act(async () => { button.click() })
  expect(state.chip).toBeNull()
})
