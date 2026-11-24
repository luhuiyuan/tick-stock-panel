import { expect, it } from 'vitest'
import type { SignalForwardEvent } from '@/lib/api'
import { caseMarks } from './SignalForwardPerformance'

it('only marks exact dates and does not draw an unevaluable exit', () => {
  const bars = ['2026-01-05', '2026-01-07', '2026-01-08'].map(date => ({
    date, open: 1, low: 1, high: 1, close: 1,
  }))
  const row = {
    bars, signal_date: '2026-01-05', entry_date: '2026-01-06',
    exit_date_1d: '2026-01-07', return_1d: 0,
    exit_date_5d: '2026-01-08', return_5d: null,
  } as SignalForwardEvent
  expect(caseMarks(row, 1).map(mark => mark.date)).toEqual(['2026-01-05', '2026-01-07'])
  expect(caseMarks(row, 5).map(mark => mark.date)).toEqual(['2026-01-05'])
  expect(caseMarks({ ...row, entry_date: '2026-01-07', execution_reason: 'entry_outside_split' }, 5))
    .toHaveLength(1)
})
