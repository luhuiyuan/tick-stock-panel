import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { DatePicker } from '@/components/DatePicker'

function fmtDate(v: string | null | undefined) {
  return v ? v.slice(0, 10) : '—'
}
function n(v: number | undefined) {
  return v == null ? '—' : v.toLocaleString()
}

/**
 * 涨停价缓存面板。纯本地规则计算(除权日用权息修正昨收), 无外部接口依赖。
 * 手动触发 + 增量续传:
 * - 已缓存的 (symbol, date) 自动跳过, 重复点击不重复算;
 * - 起始日默认取日K最早日, 可改; 结束日默认最新交易日;
 * - 进度/日志仿覆盖榜重算, running 时 1.5s 轮询。
 */
export function LimitPricePanel({ onStart }: { onStart?: () => void }) {
  const status = useQuery({
    queryKey: QK.limitPriceStatus,
    queryFn: api.limitPriceStatus,
    refetchInterval: q =>
      ['pending', 'running'].includes(q.state.data?.job?.status ?? '') ? 1500 : 15000,
  })
  const days = useQuery({ queryKey: ['limit-price-days'], queryFn: api.limitPriceDays })

  const [startDate, setStartDate] = useState<string>('')
  const [endDate, setEndDate] = useState<string>('')

  // 默认起始日 = 日K最早日(数据到达后填入), 结束日 = 最新交易日
  useEffect(() => {
    if (!startDate && days.data?.earliest) setStartDate(days.data.earliest)
    if (!endDate && days.data?.latest) setEndDate(days.data.latest)
  }, [days.data, startDate, endDate])

  const start = useMutation({
    mutationFn: () => api.startLimitPriceCache(startDate || undefined, endDate || undefined),
    onSuccess: () => {
      void status.refetch()
      onStart?.()
    },
  })
  const cancel = useMutation({
    mutationFn: () => api.cancelLimitPriceCache(),
    onSuccess: () => void status.refetch(),
  })

  const st = status.data
  const job = st?.job
  const busy = ['running', 'pending'].includes(job?.status ?? '') || start.isPending
  const dateInvalid = !!startDate && !!endDate && startDate > endDate
  const canStart = !busy && !dateInvalid

  return (
    <div className="space-y-3">
      {/* 概况 */}
      <div className="rounded-card bg-accent/8 border border-accent/20 p-3 space-y-1.5">
        <div className="text-xs text-foreground">
          按 (标的, 交易日) 缓存本地规则推断的涨跌停价：除权除息日用权息四元组修正昨收，对齐官方口径，全程不依赖外部接口。
        </div>
        <div className="text-[10px] text-muted leading-relaxed">
          增量续传：已缓存的键自动跳过，重复点击不会重复计算。首次可先缓存近 1 年，之后改起始日向前扩展。
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded-btn border border-border bg-base/40 px-2 py-1.5">
          <div className="text-[10px] text-muted">推断价键</div>
          <div className="font-mono text-secondary">{n(st?.inferred_keys)}</div>
        </div>
        <div className="rounded-btn border border-border bg-base/40 px-2 py-1.5">
          <div className="text-[10px] text-muted">缓存区间</div>
          <div className="font-mono text-secondary text-[11px]">
            {fmtDate(st?.earliest)} ~ {fmtDate(st?.latest)}
          </div>
        </div>
      </div>

      {/* 日期范围 */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-secondary">起始日期</span>
        <DatePicker value={startDate} onChange={setStartDate} max={endDate || undefined} align="right" buttonClassName="font-mono text-xs" />
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-secondary">结束日期</span>
        <DatePicker value={endDate} onChange={setEndDate} min={startDate || undefined} align="right" buttonClassName="font-mono text-xs" />
      </div>
      {dateInvalid && <p className="text-[10px] text-danger">起始日期不能晚于结束日期</p>}

      <button
        onClick={() => start.mutate()}
        disabled={!canStart}
        className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-btn bg-accent/90 text-white text-xs font-medium hover:bg-accent disabled:opacity-40 disabled:pointer-events-none transition-colors duration-150"
      >
        {start.isPending ? (
          <><Loader2 className="h-3 w-3 animate-spin" />请求中…</>
        ) : busy ? (
          <><Loader2 className="h-3 w-3 animate-spin" />缓存进行中…</>
        ) : (
          <>开始缓存 / 续传</>
        )}
      </button>

      {busy && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <span role="status" className="text-xs text-secondary">正在缓存 · {job?.progress ?? 0}%</span>
            <button className="text-[11px] text-danger hover:underline" onClick={() => cancel.mutate()} disabled={cancel.isPending}>取消</button>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-elevated">
            <div className="h-full bg-accent transition-all" style={{ width: `${job?.progress ?? 0}%` }} />
          </div>
        </div>
      )}

      {job?.log && job.log.length > 0 && (
        <div className="rounded-btn bg-base/60 border border-border max-h-40 overflow-y-auto px-2 py-1.5 font-mono text-[10px] space-y-0.5">
          {job.log.slice(-12).map((line, i) => (
            <div key={`${line.ts}-${i}`} className="text-secondary">
              <span className="text-muted">[{line.stage}]</span> {line.msg}
            </div>
          ))}
        </div>
      )}

      {(status.isError || start.isError || job?.status === 'failed') && (
        <p role="alert" className="text-[11px] text-danger">
          {String(start.error ?? status.error ?? job?.error ?? '任务失败')}
        </p>
      )}
      {job?.status === 'succeeded' && (
        <p className="text-[11px] text-success">缓存完成</p>
      )}
    </div>
  )
}
