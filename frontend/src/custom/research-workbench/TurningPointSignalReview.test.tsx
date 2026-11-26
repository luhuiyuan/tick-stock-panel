// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, test, vi } from 'vitest'
import { TurningPointSignalReview } from './TurningPointSignalReview'

vi.mock('@/components/EChartsCandlestick', () => ({
  EChartsCandlestick: ({ data, markers }: { data: unknown[]; markers: unknown[] }) => <div>放大图 {data.length} 根 · {markers.length} 个标记</div>,
}))

const bars = ['2025-03-03', '2025-03-06', '2025-03-27'].map(date => ({ date, open: 10, high: 11, low: 9, close: 10, volume: null }))
const signal = { signal_id: 'n_day_low', name: '60日新低', type: 'atom', timeframe: 'daily', enabled: true }
const summary = { ...signal, pivot_coverage: 1, alert_match_rate: 1, covered_count: 1, missed_count: 0, matched_alert_count: 1, duplicate_alert_count: 0,
  unmatched_alert_count: 0, evaluated_symbols: 1, lead_windows: { '1_5': 1, '6_15': 0, '16_30': 0 } }
const envelope = { snapshot_sha256: 'a'.repeat(64), errors: [], missing_market_partitions: [] }
const example = { case_id: 'example', case_type: 'matched', symbol: '600519.SH', direction: 'low', lead_bars: 3,
  alert: { start_date: bars[0].date, trigger_dates: [bars[0].date] }, pivot: { date: bars[1].date, confirmed_date: bars[2].date }, bars }

afterEach(() => vi.unstubAllGlobals())

test('requests one batch per page and uses that payload for zoom and event details', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  const fetcher = vi.fn(async (path: string) => {
    const url = new URL(path, 'http://localhost')
    let data: unknown
    if (url.pathname.endsWith('/signals')) data = { signals: [signal] }
    else if (url.pathname.endsWith('/cases')) {
      if (url.searchParams.get('case_type') === 'missed') return new Response('{"detail":"stale"}', { status: 409 })
      data = { ...envelope, total: 25, cases: [example] }
    } else data = { ...envelope, summary: ['high', 'low'].map(direction => ({ ...summary, direction })), results: [] }
    return new Response(JSON.stringify(data))
  })
  vi.stubGlobal('fetch', fetcher)
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient()
  const buttons = () => [...container.querySelectorAll('button')]
  const click = async (name: string) => {
    const button = buttons().find(b => b.textContent?.includes(name) || b.getAttribute('aria-label')?.includes(name))
    expect(button, name).toBeDefined()
    await act(async () => { button!.click() })
  }
  async function wait(check: () => void) {
    await vi.waitFor(async () => {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
      check()
    })
  }
  const batch = () => fetcher.mock.calls.map(([url]) => url).filter(url => url.includes('/cases?'))
  try {
    await act(async () => { root.render(<QueryClientProvider client={client}><TurningPointSignalReview/></QueryClientProvider>) })
    await click('选择信号')
    await wait(() => expect(container.querySelector('input[type=checkbox]')).not.toBeNull())
    await act(async () => { container.querySelector<HTMLInputElement>('input[type=checkbox]')!.click() })
    await click('完成')
    await act(async () => {
      const inputs = [...container.querySelectorAll('form input')]
      inputs.forEach((input, index) => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, ['600519.SH', '2025-01-01', '2025-06-01'][index])
        input.dispatchEvent(new Event('input', { bubbles: true }))
      })
    })
    await click('开始评估')
    await wait(() => expect(container.textContent).toContain('60日新低'))
    await click('L2 高点')
    await click('L2 低点')
    expect(fetcher.mock.calls.filter(([url]) => url.includes('/turning-point-review?'))).toHaveLength(1)
    await click('60日新低')
    await click('命中案例')
    await wait(() => expect(container.querySelector('svg[aria-label="研究区间缩略K线"]')).not.toBeNull())
    expect(batch()).toHaveLength(1)
    expect(batch()[0]).toContain('include_bars=true')
    expect(batch()[0]).toContain(`snapshot_sha256=${envelope.snapshot_sha256}`)
    await click('放大 K 线')
    expect(container.textContent).toContain('放大图 3 根 · 3 个标记')
    await click('事件信息')
    expect(container.textContent).toContain('局部确认：2025-03-27')
    await click('返回案例列表')
    expect(batch()).toHaveLength(1)
    await click('下一页')
    await wait(() => expect(batch()).toHaveLength(2))
    expect(batch()[1]).toContain('offset=24')
    await click('漏报拐点')
    await wait(() => expect(container.querySelector('[role=alert]')?.textContent).toContain('请重新评估'))
  } finally {
    await act(async () => root.unmount())
    client.clear()
    container.remove()
  }
})
