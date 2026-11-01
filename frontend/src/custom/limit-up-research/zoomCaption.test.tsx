// @vitest-environment jsdom
// 回归: 放大图标题曾把后端的 from_date(首板前一年的「理论窗口起点」)当成图上首根日期。
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
      bars_before: 30,
      bars_after: 30,
      full_window: true,
      window_cutoff: '2026-09-10',
      computed: true,
      rows: [{
        id: '000410.SZ:2026-05-12',
        symbol: '000410.SZ',
        name: '沈阳机床',
        start_date: '2026-05-12',
        end_date: '2026-05-15',
        boards: 3,
        days: 3,
        bars: ['2026-04-10', '2026-05-12', '2026-05-15', '2026-05-20'].map(bar),
      }],
    },
    // 后端按「首板前一年 / 结束后一年」给窗口, 但本地日线只从 2025-11-03 开始。
    ZOOM: {
      symbol: '000410.SZ',
      start_date: '2026-05-12',
      end_date: '2026-05-15',
      from_date: '2025-05-12',
      to_date: '2027-05-15',
      bars_before: 124,
      bars_after: 113,
      bars: ['2025-11-03', '2026-04-10', '2026-05-12', '2026-05-15', '2026-10-30'].map(bar),
    },
  }
})

vi.mock('@/components/EChartsCandlestick', () => ({
  EChartsCandlestick: () => <div data-testid="candles" />,
}))

vi.mock('@/lib/api', () => ({
  api: {
    limitUpResearch: vi.fn(async () => LIST),
    limitUpResearchZoom: vi.fn(async () => ZOOM),
    limitUpResearchRebuild: vi.fn(async () => ({ ok: true, total: 1, as_of: '2026-10-30' })),
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
  const card = [...host.querySelectorAll('button')].find(b => b.textContent?.includes('沈阳机床'))
  expect(card).toBeDefined()
  await act(async () => { card!.click() })
  await act(async () => { await new Promise(r => setTimeout(r, 80)) })
}

it('放大图标题写实际首根, 不写图上不存在的理论窗口起点', async () => {
  await renderPage()
  await openFirstCard()
  const text = host.textContent ?? ''
  expect(text).toContain('放大图 2025-11-03 ~ 2026-10-30')
  expect(text).not.toContain('放大图 2025-05-12')
})

it('前面 K 线不足一年时明确说出来, 与后面不足的提示对称', async () => {
  await renderPage()
  await openFirstCard()
  const text = host.textContent ?? ''
  expect(text).toContain('首板前一年要 2025-05-12，本地日线只到 2025-11-03')
  expect(text).toContain('一年要到 2027-05-15，最新行情只到 2026-10-30')
})
