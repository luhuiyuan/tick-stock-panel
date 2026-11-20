// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { caseText, CoverageCaseCard } from './CoverageCaseCard'
import { copyText } from '@/lib/clipboard'
vi.mock('@/lib/clipboard', () => ({ copyText: vi.fn() }))
const row = {category:'hit' as const, id:'case-2',name:'样例',symbol:'000001.SZ',dates:['2026-03-09','2026-03-10','2026-03-11','2026-03-12','2026-03-13'],flags:[false,null,true,false,false],start_date:'2026-03-16',end_date:'2026-03-20',bars:[]}
const summary = {signal_id:'test', name:'测试信号', version:'v2'}
it.each([['hit','已覆盖'],['miss','未覆盖'],['unknown','未知']] as const)('formats %s case', (category,label)=>{
  const text=caseText({row:{...row,category},summary})
  expect(text.split('\n')).toEqual(['信号：测试信号（test）','股票：样例（000001.SZ）','周期：日 K｜猛拉区间：2026-03-16 至 2026-03-20',`结果：${label}`,'启动前 5 个交易日：2026-03-09 未触发｜2026-03-10 未知｜2026-03-11 触发｜2026-03-12 未触发｜2026-03-13 未触发','案例 ID：case-2｜计算版本：v2'])
})
it('copies without zoom, reports failure and allows retry', async()=>{
  Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});const host=document.createElement('div'), root=createRoot(host),open=vi.fn()
  try {
    await act(async()=>root.render(<CoverageCaseCard row={row} summary={summary} onOpen={open}/>))
    const [zoom,copy]=host.querySelectorAll('button');vi.mocked(copyText).mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    await act(async()=>copy.click());expect(host.textContent).toContain('复制失败');expect(host.textContent).not.toContain('已复制')
    await act(async()=>copy.click());expect(host.textContent).toContain('已复制');expect(copyText).toHaveBeenCalledWith(caseText({row,summary}));expect(open).not.toHaveBeenCalled()
    zoom.click();expect(open).toHaveBeenCalledWith(row.id)
  } finally { await act(async()=>root.unmount()) }
})

it('labels the bar rule and does not invent missing history dates', () => {
  const summary = { signal_id:'test', name:'测试信号', version:'bars', rule:'surge-pre5-bars-v3' }
  expect(caseText({ row: { ...row, dates: ['2026-03-09'], flags: [true] }, summary })).toContain('启动前 5 根日 K：2026-03-09 触发（仅 1 根）')
  expect(caseText({ row: { ...row, dates: [], flags: [] }, summary })).toContain('启动前 5 根日 K：无历史日 K')
})

it('charts zero or one observation without undefined chart bounds', async () => {
  const { coveragePreview } = await import('./coveragePreview')
  const empty = coveragePreview({ ...row, dates: [], flags: [] }, '信号', 'surge-pre5-bars-v3')
  expect(empty.dateRange.start).toBe(row.start_date)
  expect(empty.ranges).toHaveLength(1)
  const one = coveragePreview({ ...row, dates: [row.dates[0]], flags: [true] }, '信号', 'surge-pre5-bars-v3')
  expect(one.ranges[0]).toMatchObject({ start: row.dates[0], end: row.dates[0], label: '启动前 5 根日 K' })
  expect(one.markers.map(m => m.date)).toEqual([row.dates[0], row.start_date])
})
