import type { ChanCoverage } from '@/lib/api'
import { cnSignal, STRUCTURE_COVERAGE_REASON_LABELS } from '@/lib/signals'

export function ChanCoverageSummary({ coverage }: { coverage?: ChanCoverage }) {
  if (!coverage) return null
  return <section className="min-w-0 rounded-card border border-border bg-surface p-3 space-y-2">
    <h3 className="text-xs font-medium text-foreground">{coverage.engine ?? 'chan.py'} 可计算性摘要 · {coverage.version}</h3>
    <p className="text-[11px] leading-5 text-muted">按标的 × 交易日逐信号统计。不可计算不等于未命中；首次可计算仅建立基线，不触发交易。</p>
    {!coverage.signals.length ? <p className="text-xs text-muted">本次未返回逐信号可计算性数据。</p> : <div className="overflow-x-auto"><table className="w-full min-w-[560px] text-left text-[11px]"><thead className="text-muted"><tr><th className="py-2 pr-3 font-medium">信号</th><th className="py-2 pr-3 font-medium">可计算行</th><th className="py-2 pr-3 font-medium">不可计算行</th><th className="py-2 pr-3 font-medium">受影响标的</th><th className="py-2 font-medium">原因 / 次数</th></tr></thead><tbody>{coverage.signals.map(signal => <tr key={signal.signal_id} className="border-t border-border/50 text-secondary"><td className="py-2 pr-3">{cnSignal(signal.signal_id)}</td><td className="py-2 pr-3">{signal.ready_rows}</td><td className="py-2 pr-3">{signal.unavailable_rows}</td><td className="py-2 pr-3">{signal.unavailable_symbols}</td><td className="py-2">{Object.entries(signal.reasons).map(([reason, count]) => <div key={reason}>{STRUCTURE_COVERAGE_REASON_LABELS[reason] ?? `其他原因（${reason}）`}：{count}</div>)}</td></tr>)}</tbody></table></div>}
  </section>
}
