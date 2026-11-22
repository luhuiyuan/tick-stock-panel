import { Plus, Trash2 } from 'lucide-react'
import type { SignalExpr } from '@/lib/api'

// 复合信号 expr 的树形编辑器 (受控)。
// 节点两类: 原子引用 {ref, days} 或 布尔组合 {op:'and'|'or'|'not', args}。
// 与后端 custom_signals._compile_expr / _validate_expr 结构同构。

interface Props {
  expr: SignalExpr | null | undefined
  onChange: (next: SignalExpr) => void
  /** 可引用的原子信号 (id + name) */
  atoms: { id: string; name: string }[]
  maxDays?: number
  depth?: number
}

const DEFAULT_REF = (atoms: { id: string }[]): SignalExpr => ({
  ref: atoms[0]?.id ?? '',
  days: 0,
})

export function ExprEditor({ expr, onChange, atoms, maxDays = 60, depth = 0 }: Props) {
  const node: SignalExpr = expr ?? DEFAULT_REF(atoms)
  const isRef = 'ref' in node
  const op = isRef ? undefined : node.op

  const setOp = (next: 'ref' | 'and' | 'or' | 'not') => {
    if (next === 'ref') {
      onChange(DEFAULT_REF(atoms))
    } else if (next === 'not') {
      onChange({ op: 'not', args: [DEFAULT_REF(atoms)] })
    } else {
      onChange({ op: next, args: [DEFAULT_REF(atoms), DEFAULT_REF(atoms)] })
    }
  }

  const setChild = (idx: number, child: SignalExpr) => {
    if (isRef) return
    const args = [...node.args] as SignalExpr[]
    args[idx] = child
    // TS: and/or 的 args 为 SignalExpr[], not 为单元素元组; 统一断言
    onChange({ op: node.op, args } as SignalExpr)
  }

  const addChild = () => {
    if (isRef) return
    onChange({ op: node.op, args: [...node.args, DEFAULT_REF(atoms)] } as SignalExpr)
  }

  const removeChild = (idx: number) => {
    if (isRef) return
    if (node.op === 'not') return // not 必须恰好 1 个
    if (node.args.length <= 2) return // and/or 至少 2 个
    onChange({ op: node.op, args: node.args.filter((_, i) => i !== idx) } as SignalExpr)
  }

  return (
    <div className={`rounded-md border border-white/10 bg-white/[0.02] p-2 ${depth > 0 ? 'ml-3 mt-2' : ''}`}>
      <div className="flex items-center gap-2 mb-2">
        <select
          aria-label="节点类型"
          value={isRef ? 'ref' : op}
          onChange={e => setOp(e.target.value as 'ref' | 'and' | 'or' | 'not')}
          className="h-7 rounded border border-white/15 bg-bg-elevated px-2 text-xs text-text-primary"
        >
          <option value="ref">引用原子</option>
          <option value="and">且 (AND)</option>
          <option value="or">或 (OR)</option>
          <option value="not">非 (NOT)</option>
        </select>
        {!isRef && op !== 'not' && (
          <button type="button" onClick={addChild} title="添加子节点"
            className="inline-flex items-center gap-1 text-[11px] text-accent hover:text-accent/80">
            <Plus size={12} /> 添加
          </button>
        )}
      </div>

      {isRef ? (
        <div className="flex items-center gap-2">
          <select
            aria-label="原子信号"
            value={node.ref}
            onChange={e => onChange({ ...node, ref: e.target.value })}
            className="h-7 flex-1 rounded border border-white/15 bg-bg-elevated px-2 text-xs text-text-primary"
          >
            {atoms.length === 0 && <option value="">（暂无原子信号）</option>}
            {atoms.map(a => <option key={a.id} value={a.id}>{a.name} ({a.id})</option>)}
          </select>
          <span className="text-[11px] text-text-muted">前</span>
          <input
            aria-label="前N日"
            type="number" min={0} max={maxDays}
            value={node.days ?? 0}
            onChange={e => onChange({ ...node, days: Math.max(0, Math.min(maxDays, Number(e.target.value) || 0)) })}
            className="h-7 w-14 rounded border border-white/15 bg-bg-elevated px-2 text-xs text-text-primary"
          />
          <span className="text-[11px] text-text-muted">日</span>
        </div>
      ) : (
        <div className="space-y-1">
          {node.args.map((child, i) => (
            <div key={i} className="flex items-start gap-1">
              <ExprEditor
                expr={child}
                onChange={c => setChild(i, c)}
                atoms={atoms}
                maxDays={maxDays}
                depth={depth + 1}
              />
              {op !== 'not' && node.args.length > 2 && (
                <button type="button" onClick={() => removeChild(i)} title="删除子节点"
                  className="mt-1 text-text-muted hover:text-red-400">
                  <Trash2 size={13} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
