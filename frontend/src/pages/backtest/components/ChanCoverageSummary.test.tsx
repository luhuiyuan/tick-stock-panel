// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { expect, it } from 'vitest'
import { ChanCoverageSummary } from './ChanCoverageSummary'

it('explains calculability separately from no-hit and shows reasons', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  const host = document.createElement('div'); document.body.append(host)
  const root: Root = createRoot(host)
  await act(async () => root.render(<ChanCoverageSummary coverage={{ engine: 'chan.py', version: 'test', signals: [{ signal_id: 'signal_chan_bi_1_buy', ready_rows: 12, unavailable_rows: 3, unavailable_symbols: 2, reasons: { baseline: 2, insufficient_structure: 1 } }] }} />))
  expect(host.textContent).toContain('不可计算不等于未命中')
  expect(host.textContent).toContain('可计算行')
  expect(host.textContent).toContain('首次就绪基线：2')
  expect(host.textContent).toContain('笔结构不足：1')
  await act(async () => root.unmount()); host.remove()
})
