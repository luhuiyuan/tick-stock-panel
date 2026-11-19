import { expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { api } from '@/lib/api'
import { surgeRecipe } from './surge'
import { riseRecipe } from './rise'

vi.mock('@/lib/api', () => ({api: {surgeRuns: vi.fn(), surgeRebuild: vi.fn()}}))
vi.mock('../shared/StartCountChart', () => ({StartCountChart: () => null}))

it('queries only the new snapshot and highlights actual surge days, not the baseline day', async () => {
  const ctx = {start: '', end: '', q: '', sort: 'daily_gain', order: 'desc' as const, limit: 24, offset: 0,
    extra: { ...surgeRecipe.initialExtra, started: '2025-12' }}
  vi.mocked(api.surgeRuns).mockResolvedValue({computed: true, as_of: '2026-01-05', total: 1, rows: [{
    id: 'sample', symbol: '688011.SH', name: '', start_date: '2025-12-26', end_date: '2025-12-31',
    gain: .22, daily_gain: .051, days: 4, base_close: 100, end_close: 122, pending: false, bars: [],
  }]})
  const page = await surgeRecipe.fetchPage(ctx)
  expect(Object.fromEntries(vi.mocked(api.surgeRuns).mock.calls[0][0])).toMatchObject({pending:'false', started:'2025-12', sort:'daily_gain'})
  expect(api.surgeRebuild).not.toHaveBeenCalled()
  expect(surgeRecipe.id).not.toBe(riseRecipe.id)
  const row = page.rows[0]; const range = {start:'2025-12-26',end:'2025-12-31'}
  expect(surgeRecipe.mini(row,ctx).band).toEqual(range)
  expect(surgeRecipe.dialogDecor(row,ctx,'span').ranges[0]).toMatchObject(range)
  const text = renderToStaticMarkup(<>{surgeRecipe.card(row,ctx)}</>)
  for (const value of ['688011.SH','已结束','22.00%','5.10%','4 个交易日']) expect(text).toContain(value)
})
