// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CzscPage from './CzscPage'

const fixture = vi.hoisted(() => ({ requests: [] as { symbol: string; timeframe: string; assetType?: string; resolve: (data: any) => void; reject: (error: Error) => void }[] }))
vi.mock('@/lib/api', () => ({ api: { czscChart: (symbol: string, timeframe: string, assetType?: string) => new Promise((resolve, reject) => fixture.requests.push({ symbol, timeframe, assetType, resolve, reject })) } }))
vi.mock('@/components/financials/StockFinancialSearch', () => ({ StockFinancialSearch: ({ onSelect, assetTypes }: any) => <div data-search-assets={assetTypes}><button onClick={() => onSelect('600001.SH', '样例股票', 'stock')}>选股票</button><button onClick={() => onSelect('000300.SH', '沪深300', 'index')}>选指数</button></div> }))
vi.mock('@/components/EChartsCandlestick', () => ({ EChartsCandlestick: ({ data, structures, onDateClick }: any) => <button data-chart data-structures={JSON.stringify(structures)} onClick={() => onDateClick(data[0].date)}>{data[0].close}|结构{structures.segments.length}</button> }))
let host: HTMLDivElement, root: Root, client: QueryClient
beforeEach(() => { Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); fixture.requests.length = 0; host = document.createElement('div'); document.body.append(host); root = createRoot(host); client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } }) })
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove() })
function Location() { return <output data-url>{useLocation().search}</output> }
async function mount(entry = '/czsc?symbol=600000.SH') { await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><CzscPage /><Location /></MemoryRouter></QueryClientProvider>)) }
const tick = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) }) }
const ready = (symbol = '600000.SH', close = 10) => ({ symbol, name: symbol, asset_type: 'stock', timeframe: '1d', status: 'ready', reason: null, cutoff: '2024-01-02', version: '1.0.1', price_basis: '本地价格', analysis_bars: 1, input_count: 1, input_start: '2024-01-01', input_end: '2024-01-02', rows: [{ date: '2024-01-02', open: close, high: close, low: close, close, volume: 10, amount: 20, macd_dif: null, macd_dea: null, macd_hist: null }], strokes: [{ start: '2024-01-01', end: '2024-01-02', start_price: close - 1, end_price: close }], centers: [{ start: '2024-01-01', end: '2024-01-02', low: close - 1, high: close }], signals: [] })

it('loads selected timeframe, uses independent query keys and ignores late previous-period results', async () => {
  await mount();
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '30分')!.click())
  expect(fixture.requests.map(r => r.timeframe)).toEqual(['1d', '30m'])
  fixture.requests[1].resolve({ ...ready('600000.SH', 30), timeframe: '30m' }); await tick()
  fixture.requests[0].resolve(ready()); await tick()
  expect(host.querySelector('[data-chart]')?.textContent).toBe('30|结构1')
  expect(host.querySelector('[data-url]')?.textContent).toContain('timeframe=30m')
})

it.each(['1m', '5m', '30m', '1w'])('accepts direct %s links', async timeframe => {
  await mount(`/czsc?symbol=600000.SH&timeframe=${timeframe}`)
  expect(fixture.requests[0].timeframe).toBe(timeframe)
})

it('selects index, disallows minute periods, and moves minute index selection to daily', async () => {
  await mount('/czsc?symbol=600000.SH&timeframe=30m')
  await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === '选指数')!.click())
  expect(fixture.requests[1]).toMatchObject({ symbol: '000300.SH', timeframe: '1d', assetType: 'index' })
  fixture.requests[1].resolve({ ...ready('000300.SH', 3500), asset_type: 'index' }); await tick()
  expect(([...host.querySelectorAll('[role=group] button')].find(b => b.textContent === '30分') as HTMLButtonElement | undefined)?.disabled).toBe(true)
})

it('renders segment centers, candidate points and confirmed event timing', async () => {
  await mount(); fixture.requests[0].resolve({ ...ready(), segments: [{ start: '2024-01-01', end: '2024-01-02', start_price: 9, end_price: 10, confirmed: true }], segment_centers: [{ start: '2024-01-01', end: '2024-01-02', low: 8, high: 9 }], bsp_points: [{ level: 'seg', date: '2024-01-01', price: 9, types: ['1p'], is_buy: true, status: 'candidate', first_seen_at: '2024-01-01' }], signals: [{ date: '2024-01-02', signal_id: 'signal_chan_bi_1_buy', price: 10, endpoint_at: '2024-01-01', first_seen_at: '2024-01-01', confirmed_at: '2024-01-02' }] }); await tick()
  const layers = JSON.parse(host.querySelector('[data-chart]')!.getAttribute('data-structures')!)
  expect(layers.segments).toHaveLength(2); expect(layers.boxes).toHaveLength(2); expect(layers.points.some((p: any) => p.candidate)).toBe(true)
  expect(host.textContent).toContain('端点 2024-01-01'); expect(host.textContent).toContain('确认 2024-01-02')
})

it('does not expose a minute synchronization action', async () => {
  await mount('/czsc?symbol=600000.SH&timeframe=5m'); fixture.requests[0].resolve({ status: 'empty', reason: '暂无本地分钟行情' }); await tick()
  expect(host.textContent).toContain('仅读取本机已存行情'); expect(host.textContent).not.toContain('同步分钟数据')
})
