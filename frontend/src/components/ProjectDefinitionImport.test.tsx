// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import { ProjectDefinitionImport } from './ProjectDefinitionImport'
import { api, type ProjectDefinitionEntry } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

function entry(kind: 'factor' | 'signal', id: string, label: string, action: ProjectDefinitionEntry['action'], required: string[] = []): ProjectDefinitionEntry {
  return { key: `${kind}:${id}`, kind, id, label, action, required, path: `${id}.json`, dependencies: required, local_dependencies: [], issues: [] }
}

it('selects only missing signals, shows their dependencies and refreshes both libraries after install', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  const signal = entry('signal', 'demo', '示例信号', 'create', ['factor:uf_demo'])
  const conflict = entry('signal', 'local', '本机冲突', 'conflict')
  const factor = entry('factor', 'uf_demo', '配套因子', 'create')
  const preview = vi.spyOn(api, 'projectDefinitions')
    .mockResolvedValueOnce({ revision: 'before', entries: [signal, conflict, factor] })
    .mockResolvedValue({ revision: 'after', entries: [{ ...signal, action: 'reuse' }, conflict, { ...factor, action: 'reuse' }] })
  const apply = vi.spyOn(api, 'projectDefinitionsApply').mockResolvedValue({
    imported: 2, reused: 0, counts: { strategy: 0, signal: 1, factor: 1 }, resources: [signal.key, factor.key], changed: [signal.key, factor.key],
  })
  const qc = new QueryClient()
  const invalidate = vi.spyOn(qc, 'invalidateQueries')
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const button = (text: string) => Array.from(document.querySelectorAll('button')).find(b => b.textContent?.includes(text))!
  const click = async (text: string) => { await act(async () => { button(text).click() }) }
  try {
    await act(async () => root.render(<QueryClientProvider client={qc}><ProjectDefinitionImport kind="signal" /></QueryClientProvider>))
    await click('导入项目定义')
    await vi.waitFor(async () => { await act(async () => {}); expect(document.body.textContent).toContain('示例信号') })
    expect(document.body.textContent).toContain('配套导入：配套因子')
    expect(document.body.textContent).not.toContain('本机冲突')
    await click('同 ID 冲突')
    expect(document.body.textContent).toContain('保留本机定义，不可覆盖')
    expect(document.querySelector('input[type="checkbox"]')).toBeNull()
    await click('可导入 1')
    await click('全选可导入项')
    expect(document.body.textContent).toContain('另含 1 个缺失依赖')
    await click('确认导入（1）')
    await vi.waitFor(async () => { await act(async () => {}); expect(document.body.textContent).toContain('已导入 2 项') })
    expect(apply).toHaveBeenCalledWith('signal', ['demo'], 'before')
    expect(preview).toHaveBeenCalledTimes(2)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: QK.customSignals })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: QK.factorLibrary('all').slice(0, 1) })
  } finally {
    await act(async () => root.unmount())
    host.remove()
    qc.clear()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  }
})
