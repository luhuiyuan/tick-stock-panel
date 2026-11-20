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
