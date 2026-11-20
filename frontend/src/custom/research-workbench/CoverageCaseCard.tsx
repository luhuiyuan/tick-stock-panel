import { useState } from 'react'
import type { CoverageEvent, CoverageSummary } from '@/lib/api'
import { copyText } from '@/lib/clipboard'
import { MiniChart } from './shared/MiniChart'

export const coverageLabels = { hit: '已覆盖', miss: '未覆盖', unknown: '未知' } as const

type Props = {
  row: CoverageEvent
  summary: Pick<CoverageSummary, 'signal_id' | 'name' | 'version'>
  onOpen: (id: string) => void
}

export function caseText({ row, summary }: Omit<Props, 'onOpen'>) {
  const days = row.dates.map((d, i) => `${d} ${row.flags[i] == null ? '未知' : row.flags[i] ? '触发' : '未触发'}`)
  return [
    `信号：${summary.name}（${summary.signal_id}）`,
    `股票：${row.name}（${row.symbol}）`,
    `周期：日 K｜猛拉区间：${row.start_date} 至 ${row.end_date}`,
    `结果：${coverageLabels[row.category]}`,
    `启动前 5 个交易日：${days.join('｜')}`,
    `案例 ID：${row.id}｜计算版本：${summary.version}`,
  ].join('\n')
}

export function CoverageCaseCard({ row, summary, onOpen }: Props) {
  const [feedback, setFeedback] = useState('')
  const dates = new Set(row.bars.map(b => b.date))
  async function copy() {
    try {
      setFeedback(await copyText(caseText({ row, summary })) ? '已复制' : '复制失败，请重试')
    } catch { setFeedback('复制失败，请重试') }
  }
  return <article className="relative rounded border border-border bg-surface p-2 hover:border-accent">
    <button type="button" className="block w-full text-left" aria-label={`${row.symbol} ${row.start_date} 放大 K 线`} onClick={() => onOpen(row.id)}>
      <div className="truncate pr-24 text-sm font-medium">{row.name} · {row.symbol}</div>
      <div className="text-xs text-muted">{row.start_date} — {row.end_date}</div>
      <MiniChart bars={row.bars} spec={{band:{start:row.start_date,end:row.end_date},vlines:row.dates.flatMap((d,i)=>row.flags[i]===true&&dates.has(d)?[{date:d,color:'#eab308'}]:[])}}/>
      <div className="text-xs text-muted">前五日：{row.flags.map(f => f === null ? '未知' : f ? '亮' : '否').join(' · ')}</div>
    </button>
    <button type="button" className="absolute right-2 top-1 rounded-btn bg-elevated px-2 py-1 text-xs" aria-label={`复制案例 ${row.symbol} ${row.start_date}`} onClick={copy}>复制案例</button>
    <span role="status" className="block text-right text-xs text-secondary">{feedback}</span>
  </article>
}
