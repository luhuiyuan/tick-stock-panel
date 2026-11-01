// @vitest-environment jsdom
// 回归: 放大图标题曾把后端的 from_date(起涨前一年的「理论窗口起点」)当成图上首根日期。
// 本地只存最近一年日线时, from_date 会早于实际首根, 标题显示一个图上并不存在的日期。
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import extension from './extension'

const { LIST, ZOOM } = vi.hoisted(() => {
  const bar = (date: string) => ({ date, open: 10, high: 11, low: 9, close: 10.5, volume: 1000 })
  return {
    LIST: {
      as_of: '2026-10-30',
      total: 1,
      offset: 0,
      limit: 24,
      min_gain: 0.3,
      bars_before: 30,
      bars_after: 30,
      full_window: true,
      window_cutoff: '2026-09-10',
      computed: true,
      rows: [{
        id: '001219.SZ:2026-10-22',
        symbol: '001219.SZ',
        name: '青岛食品',
        start_date: '2026-10-22',
        end_date: '2026-10-30',
        days: 7,
        gain: 0.634426,
        base_close: 12.2,
        end_close: 19.94,
        bars: ['2026-09-20', '2026-10-22', '2026-10-30'].map(bar),
      }],
    },
    // 后端按「起涨前一年 / 结束后一年」给窗口, 但本地日线只从 2025-11-03 开始。
    ZOOM: {
      symbol: '001219.SZ',
      start_date: '2026-10-22',
      end_date: '2026-10-30',
      from_date: '2025-10-22',
      to_date: '2027-10-30',
      bars_before: 234,
      bars_after: 0,
      bars: ['2025-11-03', '2026-09-20', '2026-10-22', '2026-10-30'].map(bar),
    },
  }
})

vi.mock('@/components/EChartsCandlestick', () => ({
  EChartsCandlestick: () => <div data-testid="candles" />,
}))

vi.mock('@/lib/api', () => ({
  api: {
    riseResearch: vi.fn(async () => LIST),
    riseResearchZoom: vi.fn(async () => ZOOM),
    riseResearchRebuild: vi.fn(async () => ({ ok: true, total: 1, as_of: '2026-10-30' })),
    // 空的启动分布: 让 StartCountChart 走提示分支, 不初始化 echarts 画布。
    riseResearchStarts: vi.fn(async () => ({
      bucket: 'day', full_window: true, window_cutoff: '2026-09-10', computed: true, points: [],
    })),
  },
}))

const Page = extension.routes![0].component

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
})

async function renderPage() {
  await act(async () => root.render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <Page />
      </QueryClientProvider>
    </MemoryRouter>,
  ))
  await act(async () => { await new Promise(r => setTimeout(r, 80)) })
}

async function openFirstCard() {
  const card = [...host.querySelectorAll('button')].find(b => b.textContent?.includes('青岛食品'))
  expect(card).toBeDefined()
  await act(async () => { card!.click() })
  await act(async () => { await new Promise(r => setTimeout(r, 80)) })
}

it('放大图标题写实际首根, 不写图上不存在的理论窗口起点', async () => {
  await renderPage()
  await openFirstCard()
  const text = host.textContent ?? ''
  expect(text).toContain('放大图 2025-11-03 ~ 2026-10-30')
  expect(text).not.toContain('放大图 2025-10-22')
})

it('前面 K 线不足一年时明确说出来, 与后面不足的提示对称', async () => {
  await renderPage()
  await openFirstCard()
  const text = host.textContent ?? ''
  expect(text).toContain('起涨前一年要 2025-10-22，本地日线只到 2025-11-03')
  expect(text).toContain('一年要到 2027-10-30，最新行情只到 2026-10-30')
})
