import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { Search } from 'lucide-react'
import { DatePicker } from '@/components/DatePicker'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { PageHeader } from '@/components/PageHeader'
import { MiniChart } from './shared/MiniChart'
import { ZoomDialog } from './shared/ZoomDialog'
import type { ResearchCtx, ResearchRecipe } from './shared/types'
import { RECIPES } from './recipes'

/**
 * 研究工作台的壳。
 *
 * 这里只编排「所有研究都一样」的部分: 标签切换 / 日期区间 / 搜索 / 排序 / 分页 /
 * 重建流程 / 空态与错误 / 列表网格 / 放大框。
 * 不含任何 `if (recipe.id === ...)` 之类的判断 —— 差异全部由说明卡声明或注入组件承担。
 * 新增研究: 写一张说明卡 + 注册进 RECIPES, 本文件一行不用改。
 */
export function Workbench() {
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const tab = params.get('tab') ?? RECIPES[0].id
  const recipe = RECIPES.find(item => item.id === tab) ?? RECIPES[0]
  // 保留 URL 上其它参数(公网 sharecode 等), 只改 tab
  const switchTab = (id: string) => {
    const next = new URLSearchParams(location.search)
    next.set('tab', id)
    navigate(`${location.pathname}?${next.toString()}`, { replace: true })
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b border-border px-5 py-2">
        {RECIPES.map(item => {
          const Icon = item.icon
          const active = item.id === recipe.id
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => switchTab(item.id)}
              className={`inline-flex h-8 items-center gap-1.5 rounded-btn px-3 text-xs transition-colors ${
                active ? 'bg-elevated font-medium text-foreground' : 'text-muted hover:bg-elevated hover:text-foreground'
              }`}
            >
              <Icon size={14} />{item.label}
            </button>
          )
        })}
      </div>
      {/* key 保证切换研究时所有筛选状态干净重置 */}
      <ResearchPanel key={recipe.id} recipe={recipe} />
    </div>
  )
}

function ResearchPanel({ recipe }: { recipe: ResearchRecipe }) {
  const pageSize = recipe.pageSize ?? 24
  const keyPrefix = useMemo(() => ['research-workbench', recipe.id] as const, [recipe.id])

  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [sort, setSort] = useState(recipe.defaultSort)
  const [order, setOrder] = useState<'desc' | 'asc'>('desc')
  const [draftQ, setDraftQ] = useState('')
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [extra, setExtraState] = useState<Record<string, unknown>>(() => ({ ...recipe.initialExtra }))
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobDone, setJobDone] = useState(false)

  const queryClient = useQueryClient()
  const setExtra = (patch: Record<string, unknown>) => setExtraState(current => ({ ...current, ...patch }))

  // 搜索防抖
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(draftQ.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [draftQ])

  // 只有「参与列表查询」的私有条件才进 key, 纯 UI 状态(展开面板/图表分桶)不触发重查
  const queryExtra = useMemo(() => recipe.queryExtra(extra), [extra, recipe])
  const queryExtraKey = useMemo(() => JSON.stringify(queryExtra), [queryExtra])

  useEffect(() => { setOffset(0) }, [start, end, sort, order, q, queryExtraKey])

  const ctx: ResearchCtx = useMemo(() => ({
    start, end, q, sort, order, offset, limit: pageSize, extra,
  }), [start, end, q, sort, order, offset, pageSize, extra])

  const queryKey = [...keyPrefix, 'page', [start, end, sort, order, q, offset, queryExtraKey].join('|')]
  const query = useQuery({
    queryKey,
    queryFn: () => recipe.fetchPage(ctx),
  })

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: keyPrefix })
    for (const key of recipe.invalidateKeys ?? []) queryClient.invalidateQueries({ queryKey: key as unknown[] })
  }

  const rebuild = useMutation({
    mutationFn: () => recipe.rebuild!.run(ctx),
    onSuccess: data => {
      const id = data && typeof data === 'object' && 'jobId' in data ? data.jobId : undefined
      if (id && recipe.job) {
        setJobId(id)
        setJobDone(false)
      } else {
        invalidateAll()
      }
    },
  })

  // 算得慢的研究才有: 轮询任务, 完成后统一失效
  const jobQuery = useQuery({
    queryKey: [...keyPrefix, 'job', jobId],
    enabled: Boolean(jobId) && Boolean(recipe.job) && !jobDone,
    queryFn: () => recipe.job!.poll(jobId!),
    refetchInterval: 1200,
  })
  useEffect(() => {
    const view = jobQuery.data
    if (view && (view.done || view.failed)) {
      setJobDone(true)
      invalidateAll()
    }
  }, [jobQuery.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const running = Boolean(jobId) && !jobDone
  const jobView = running ? jobQuery.data : undefined
  const rows = query.data?.rows ?? []
  const total = query.data?.total ?? 0
  const page = Math.floor(offset / pageSize) + 1
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const canRun = recipe.rebuild?.canRun ? recipe.rebuild.canRun(ctx) : true
  const busy = rebuild.isPending || running

  const slotProps = {
    ctx,
    page: query.data,
    setExtra,
    refresh: () => query.refetch(),
    rebuilding: busy,
    keyPrefix,
  }

  return (
    <>
      <PageHeader title="研究工作台" subtitle={recipe.subtitle(query.data, ctx)} />

      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-2">
        <DatePicker value={start} onChange={setStart} max={end || undefined} placeholder="开始日期" align="left" />
        <DatePicker value={end} onChange={setEnd} min={start || undefined} placeholder="结束日期" align="left" />
        {(start || end) && (
          <button type="button" className="h-8 rounded-btn px-2 text-xs text-muted hover:bg-elevated hover:text-foreground" onClick={() => { setStart(''); setEnd('') }}>清空日期</button>
        )}
        {recipe.ExtraFilters && <recipe.ExtraFilters {...slotProps} />}
        <label className="flex items-center gap-1 text-xs text-muted">
          排序
          <select className="h-8 rounded-btn bg-elevated px-2 text-foreground" value={sort} onChange={event => setSort(event.target.value)}>
            {recipe.sortOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <button type="button" className="h-8 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => setOrder(value => value === 'desc' ? 'asc' : 'desc')}>
          {order === 'desc' ? '降序' : '升序'}
        </button>
        {recipe.rebuild && (
          <button
            type="button"
            className="h-8 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground disabled:opacity-40"
            disabled={!canRun || busy}
            onClick={() => rebuild.mutate()}
          >
            {busy ? recipe.rebuild.pendingLabel : recipe.rebuild.label}
          </button>
        )}
        <label className="relative ml-auto">
          <Search className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted" />
          <input
            value={draftQ}
            onChange={event => setDraftQ(event.target.value)}
            placeholder="代码或名称"
            className="h-8 w-40 rounded-btn bg-elevated pl-7 pr-2 text-xs text-foreground outline-none"
          />
        </label>
      </div>

      {jobView?.node && <div className="border-b border-border px-5 py-2">{jobView.node}</div>}
      {rebuild.isError && <div className="border-b border-border px-5 py-2 text-xs text-danger">提交失败：{String(rebuild.error)}</div>}
      {recipe.ExtraPanel && <recipe.ExtraPanel {...slotProps} />}

      <div className="min-h-0 flex-1 overflow-auto px-5 py-3">
        {(query.isLoading || (rebuild.isPending && !recipe.job)) ? (
          <div className="py-16 text-center text-sm text-muted">{rebuild.isPending ? recipe.rebuild?.pendingLabel : '正在读取已保存的结果…'}</div>
        ) : query.isError ? (
          <ErrorState hint="结果没有读出来。" onRetry={() => query.refetch()} retrying={query.isRefetching} />
        ) : query.data?.computed === false ? (
          <EmptyState
            icon={Search}
            title={recipe.notComputed?.title ?? '还没有计算，请点重新计算'}
            hint={recipe.notComputed?.hint ?? '算完会保存下来。之后打开页面直接读取，不会因为行情更新自动重算。'}
          />
        ) : rows.length === 0 ? (
          <EmptyState icon={Search} title={recipe.empty(ctx).title} hint={recipe.empty(ctx).hint} />
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {rows.map(row => (
              <button
                key={row.id}
                type="button"
                onClick={() => setSelectedId(row.id)}
                className="rounded-lg border border-border bg-surface p-3 text-left transition-colors hover:border-accent/50"
              >
                {recipe.card(row, ctx)}
                <div className="mt-2"><MiniChart bars={row.bars} spec={recipe.mini(row, ctx)} /></div>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-border px-5 py-2 text-xs text-muted">
        <span>共 {total} 条 · 第 {page} / {pages} 页</span>
        <div className="flex items-center gap-2">
          <button type="button" className="h-8 rounded-btn bg-elevated px-2 disabled:opacity-40" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - pageSize))}>上一页</button>
          <button type="button" className="h-8 rounded-btn bg-elevated px-2 disabled:opacity-40" disabled={offset + pageSize >= total} onClick={() => setOffset(value => value + pageSize)}>下一页</button>
        </div>
      </div>

      {selectedId && rows.some(row => row.id === selectedId) && (
        <ZoomDialog
          recipe={recipe}
          rows={rows}
          currentId={selectedId}
          asOf={query.data?.asOf}
          ctx={ctx}
          keyPrefix={keyPrefix}
          onSelect={setSelectedId}
          onClose={() => setSelectedId(null)}
        />
      )}
    </>
  )
}
