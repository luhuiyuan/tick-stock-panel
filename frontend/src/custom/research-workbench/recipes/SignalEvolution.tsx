import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api, type SignalEvolutionJob, type SignalEvolutionMetrics } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

type Candidate = NonNullable<SignalEvolutionJob['result']>['candidates'][number]
const button = 'rounded-btn px-2 py-1 text-xs text-accent hover:bg-elevated disabled:opacity-50'
const active = (status?: SignalEvolutionJob['status']) => status === 'pending' || status === 'running'
const percent = (n: number | null) => n == null ? '不可评价' : `${(n * 100).toFixed(2)}%`
const message = (e: unknown) => e instanceof Error ? e.message : '请求失败，请重试'

function candidateName(candidate: Candidate) {
  if (candidate.threshold == null) return '均线多头'
  const term = `Amihud ${candidate.op} ${candidate.threshold.toPrecision(8)}`
  return candidate.kind === 'and' ? `均线多头 AND ${term}` : term
}

function Metrics({ data }: { data: SignalEvolutionMetrics }) {
  const primary = data.horizons['3']
  return (
    <div className="space-y-0.5">
      <div>{primary.status === 'evaluated' ? `${percent(primary.lift)}（D+3 相对基准）` : '样本不足，不可评价'}</div>
      <div className="text-muted">
        命中 {data.hit}／未命中 {data.miss}／未知 {data.unknown}（{percent(data.rows ? data.unknown / data.rows : null)}）
      </div>
      <div className="text-muted">目标缺失 {primary.target_missing}／跨界剔除 {primary.purged}／有效支持 {primary.support}</div>
      <div className="text-muted">有效命中 {primary.hits_with_return}（{primary.hit_days} 日） · 胜率 {percent(primary.win_rate)}</div>
      <div className="text-muted">相对基准：D+1 {percent(data.horizons['1'].lift)} · D+5 {percent(data.horizons['5'].lift)}</div>
    </div>
  )
}

export function SignalEvolution() {
  const [expanded, setExpanded] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useQuery({
    queryKey: QK.signalEvolutionJob(jobId),
    queryFn: () => api.signalEvolutionJob(jobId!),
    enabled: !!jobId,
    retry: false,
    refetchInterval: query => active(query.state.data?.status) ? 1200 : false,
  })
  const create = useMutation({ mutationFn: api.startSignalEvolution, onSuccess: response => setJobId(response.id) })
  const cancel = useMutation({
    mutationFn: api.cancelSignalEvolution,
    onSuccess: () => void job.refetch(),
    onError: () => void job.refetch(),
  })
  const data = job.data
  const busy = !!jobId && active(data?.status)
  const result = data?.result

  return (
    <section aria-label="单因子信号进化" className="border-b border-border px-5 py-2 text-xs">
      <button type="button" className={button} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        单因子信号进化（独立研究）{expanded ? ' ▲' : ' ▼'}
      </button>
      {expanded && <div className="mt-2 space-y-2">
        <p className="text-muted">固定样例：均线多头 + 20 日 Amihud 非流动性；000001.SZ、600000.SH、300750.SZ；2026-04-13 至 2026-11-03。</p>
        <p className="text-muted">股票 × 交易日，D 日收盘特征 → D+3 收盘研究收益。不沿用触板筛选、板位或出场条件，不代表可以成交，也不生成交易信号。</p>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={button} disabled={create.isPending || busy || (!!jobId && job.isPending)} onClick={() => {
            cancel.reset()
            create.mutate()
          }}>{jobId ? '重新研究' : '运行研究'}</button>
          {busy && <button type="button" className={button} disabled={cancel.isPending || cancel.isSuccess} onClick={() => cancel.mutate(jobId!)}>
            {cancel.isSuccess ? '已请求取消' : '取消任务'}
          </button>}
          <span role="status" className="text-muted">
            {create.isPending ? '正在创建…' : data ? `${data.id} · ${data.status === 'pending' ? '等待执行' : data.status === 'running'
              ? `执行中 ${data.progress}% ${data.log.at(-1)?.msg ?? ''}` : data.status === 'succeeded' ? '已完成' : '未完成'}` : jobId && job.isPending ? '正在读取状态…' : ''}
          </span>
        </div>
        {create.error && <p role="alert" className="text-danger">创建失败：{message(create.error)}</p>}
        {cancel.error && <p role="alert" className="text-danger">取消请求失败：{message(cancel.error)}</p>}
        {job.error && <div role="alert" className="text-danger">状态读取失败：{message(job.error)} <button className={button} type="button" onClick={() => void job.refetch()}>重试读取</button></div>}
        {data?.error && <p role="alert" className="text-danger">{data.error}</p>}
        {result && <>
          <p>候选 {result.attempted} 个 · 训练 {result.split.train_dates} 日 · 留出 {result.split.holdout_dates} 日（从 {result.split.holdout_start} 起） · 阈值拟合 {result.threshold_fit_count} 行</p>
          <p className="text-muted">来源：本地复权日线（{result.snapshot.observed_rows} 行），代次 {result.snapshot.generation.slice(0, 12)}。按训练排名展示；留出只评价，不作择优。此固定范围不代表全市场。</p>
          {result.factor_status !== 'ready' && <p className="text-muted">{result.factor_status === 'constant_training_values' ? '训练因子为常数' : '训练因子无有效值'}，未生成阈值候选。</p>}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[45rem] border-collapse text-left">
              <thead><tr><th scope="col" className="p-1.5">候选</th><th scope="col" className="p-1.5">训练</th><th scope="col" className="p-1.5">冻结后的留出</th></tr></thead>
              <tbody>{result.candidates.map(candidate => <tr className="border-t border-border align-top" key={candidate.id}>
                <th scope="row" className="p-1.5 font-medium">{candidateName(candidate)}</th>
                <td className="p-1.5"><Metrics data={candidate.train} /></td>
                <td className="p-1.5"><Metrics data={candidate.holdout} /></td>
              </tr>)}</tbody>
            </table>
          </div>
        </>}
      </div>}
    </section>
  )
}
