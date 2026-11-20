import { useId, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Modal } from '@/components/Modal'
import { api, type ProjectDefinitionEntry } from '@/lib/api'
import { refreshResourceQueries } from '@/lib/importCenter'

type Kind = 'factor' | 'signal'
const tabs = [
  ['available', '可导入'], ['reuse', '已安装'], ['conflict', '同 ID 冲突'], ['blocked', '依赖或校验未通过'],
] as const
type Tab = typeof tabs[number][0]
const button = 'rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:text-foreground disabled:opacity-50'
const tabFor = (entry: ProjectDefinitionEntry): Tab =>
  entry.action !== 'create' ? entry.action : entry.issues.length ? 'blocked' : 'available'
const message = (error: unknown) => error instanceof Error ? error.message : String(error)
const timeframes: Record<string, string> = { daily: '日线', intraday: '盘中' }
const previewKey = ['project-definition-import'] as const

export function ProjectDefinitionImport({ kind }: { kind: Kind }) {
  const [open, setOpen] = useState(false)
  return <>
    <button type="button" className={button} onClick={() => setOpen(true)}>导入项目定义</button>
    {open && <ImportDialog kind={kind} onClose={() => setOpen(false)} />}
  </>
}

function ImportDialog({ kind, onClose }: { kind: Kind; onClose: () => void }) {
  const title = useId()
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('available')
  const [selection, setSelection] = useState<string[]>([])
  const preview = useQuery({ queryKey: previewKey, queryFn: ({ signal }) => api.projectDefinitions(signal), retry: false })
  const apply = useMutation({
    mutationFn: ({ ids, revision }: { ids: string[]; revision: string }) => api.projectDefinitionsApply(kind, ids, revision),
    onSuccess: async () => {
      setSelection([])
      await Promise.all([refreshResourceQueries(qc), qc.invalidateQueries({ queryKey: previewKey })])
    },
  })
  const entries = preview.data?.entries ?? []
  const own = entries.filter(entry => entry.kind === kind)
  const available = own.filter(entry => tabFor(entry) === 'available')
  const selected = available.filter(entry => selection.includes(entry.id))
  const labels = new Map(entries.map(entry => [entry.key, entry.label]))
  const keys = new Set(selected.map(entry => entry.key))
  const required = [...new Set(selected.flatMap(entry => entry.required))].filter(key => !keys.has(key))
  const visible = own.filter(entry => tabFor(entry) === tab)
  const ready = !!preview.data && !preview.isFetching && !preview.isError && !apply.isPending && !apply.isError
  const reload = () => {
    setSelection([])
    apply.reset()
    void preview.refetch()
  }

  return <Modal labelledBy={title} onClose={() => { if (!apply.isPending) onClose() }} closeOnBackdrop={!apply.isPending}
    panelClassName="flex max-h-[85vh] w-[92vw] max-w-2xl flex-col rounded-card border border-border bg-surface shadow-xl">
    <header className="border-b border-border p-4">
      <h2 id={title} className="font-semibold text-foreground">导入项目{kind === 'factor' ? '因子' : '信号'}</h2>
      <p className="mt-1 text-xs text-secondary">从随版本发布的定义中补齐缺失项，不覆盖本机定义。</p>
    </header>
    <div className="flex flex-wrap gap-2 p-4" aria-label="定义状态">
      {tabs.map(([id, label]) => <button type="button" key={id} className={button} aria-pressed={tab === id}
        onClick={() => setTab(id)}>{label} {own.filter(entry => tabFor(entry) === id).length}</button>)}
    </div>
    <div className="min-h-24 overflow-auto px-4 pb-4" aria-busy={preview.isFetching || apply.isPending}>
      {preview.isFetching && <p className="text-xs text-secondary">正在核对本机定义…</p>}
      {preview.isError && <p role="alert" className="text-xs text-danger">{message(preview.error)}</p>}
      {!preview.isFetching && !preview.isError && visible.length === 0 && <p className="text-xs text-secondary">此状态没有项目定义。</p>}
      {tab === 'available' && available.length > 0 && <button type="button" className={button} disabled={!ready}
        onClick={() => setSelection(available.map(entry => entry.id))}>全选可导入项</button>}
      <ul className="divide-y divide-border">
        {visible.map(entry => <li key={entry.key} className="py-2 text-xs">
          <label className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {tab === 'available' && <input type="checkbox" checked={selection.includes(entry.id)} disabled={!ready}
              onChange={event => setSelection(current => event.target.checked ? [...current, entry.id] : current.filter(id => id !== entry.id))} />}
            <span className="font-medium text-foreground">{entry.label}</span>
            <span className="font-mono text-secondary">{entry.id}</span>
            <span className="text-secondary">{kind === 'signal' ? (timeframes[entry.detail ?? ''] ?? entry.detail) : entry.detail}</span>
          </label>
          {tab === 'available' && entry.required.length > 0 && <p className="mt-1 text-secondary">配套导入：{entry.required.map(key => labels.get(key) ?? key).join('、')}</p>}
          {entry.action === 'conflict' && <p className="mt-1 text-secondary">保留本机定义，不可覆盖。</p>}
          {entry.issues.map(issue => <p key={issue} className="mt-1 text-danger">{issue}</p>)}
        </li>)}
      </ul>
    </div>
    <footer className="space-y-2 border-t border-border p-4 text-xs text-secondary">
      {required.length > 0 && <p className="max-h-16 overflow-auto">另含 {required.length} 个缺失依赖：{required.map(key => labels.get(key) ?? key).join('、')}</p>}
      {apply.isSuccess && <p role="status">已导入 {apply.data.imported} 项，库列表已刷新。</p>}
      {apply.isError && <p role="alert">{message(apply.error)}</p>}
      <div className="flex flex-wrap justify-end gap-2">
        {(apply.isError || preview.isError) && <button type="button" className={button} onClick={reload}>重新核对</button>}
        <button type="button" className={button} onClick={onClose} disabled={apply.isPending}>关闭</button>
        <button type="button" className={`${button} text-accent`} disabled={!ready || selected.length === 0}
          onClick={() => apply.mutate({ ids: selected.map(entry => entry.id), revision: preview.data!.revision })}>
          {apply.isPending ? '导入中…' : `确认导入（${selected.length}）`}
        </button>
      </div>
    </footer>
  </Modal>
}
