import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import * as echarts from 'echarts'
import { Dna, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { useChartTheme } from '@/lib/theme'

type EvolutionResp = Awaited<ReturnType<typeof api.dabanResearchEvolution>>

const pct1 = (x: number | null | undefined) => x == null ? '—' : (x * 100).toFixed(1) + '%'
const sgn = (x: number | null | undefined) => x == null ? '—' : (x >= 0 ? '+' : '') + x.toFixed(2) + '%'

/** 把 +t_xxx / -t_xxx 文字转成带颜色的展示片段 */
function TermChips({ terms }: { terms: string[] }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      {terms.map((t, i) => {
        const neg = t.startsWith('-')
        const label = t.slice(1).replace(/^t_/, '')
        return (
          <span key={i} className={`rounded px-1 py-0.5 text-[10px] font-medium ${
            neg ? 'bg-purple-500/15 text-purple-400' : 'bg-sky-500/15 text-sky-400'
          }`}>
            {neg ? '非' : ''}{label}
          </span>
        )
      })}
    </span>
  )
}

function LayerTable({ layer, baselineWin }: {
  layer: EvolutionResp['layers'][number]
  baselineWin: number
}) {
  return (
    <div className="min-w-[240px] flex-1">
      <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold text-secondary">
        <span className="rounded bg-elevated px-1.5 py-0.5">{layer.size} 个条件</span>
        <span className="text-[10px] font-normal text-muted">{layer.beam_count} 组存活</span>
      </div>
      <div className="overflow-hidden rounded border border-border">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="bg-elevated text-[10px] text-muted">
              <th className="px-1.5 py-1 text-left font-normal">组合公式</th>
              <th className="px-1 py-1 text-right font-normal">胜率</th>
              <th className="px-1 py-1 text-right font-normal">超额</th>
              <th className="px-1 py-1 text-right font-normal">OOS</th>
              <th className="px-1 py-1 text-right font-normal">笔数</th>
            </tr>
          </thead>
          <tbody>
            {layer.top.map((r, i) => {
              const ex = r.stat.win - baselineWin
              const oos = r.stat.oos?.win
              return (
                <tr key={i} className="border-t border-border/60 hover:bg-elevated/40" title={r.key}>
                  <td className="max-w-[170px] truncate px-1.5 py-1">
                    <TermChips terms={r.terms} />
                  </td>
                  <td className={`px-1 py-1 text-right font-mono font-medium ${ex > 0 ? 'text-bull' : ex < 0 ? 'text-bear' : 'text-foreground'}`}>
                    {pct1(r.stat.win)}
                  </td>
                  <td className={`px-1 py-1 text-right font-mono ${ex > 0 ? 'text-bull' : ex < 0 ? 'text-bear' : 'text-muted'}`}>
                    {sgn(ex * 100)}
                  </td>
                  <td className="px-1 py-1 text-right font-mono text-muted" title="样本外胜率(IS/OOS 按时间 70/30 切分)">
                    {oos == null ? '—' : pct1(oos)}
                  </td>
                  <td className="px-1 py-1 text-right font-mono text-muted">{r.stat.n}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function TreeChart({ tree, baselineWin }: { tree: EvolutionResp['tree']; baselineWin: number }) {
  const box = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)
  const theme = useChartTheme()

  useEffect(() => {
    if (!box.current || tree.nodes.length === 0) return
    inst.current = inst.current ?? echarts.init(box.current, undefined, { renderer: 'canvas' })
    const data = tree.nodes.map(n => ({
      name: `第${n.size}层`,
      value: n.win,
      labelText: `${n.label}\n${pct1(n.win)}`,
      win: n.win,
      mean: n.mean,
      n: n.n,
      oos: n.oos_win,
      terms: n.terms,
      symbolSize: Math.max(8, Math.min(20, 8 + (n.win - baselineWin) * 60)),
      itemStyle: {
        color: n.win >= baselineWin
          ? (n.oos_win != null && n.oos_win >= baselineWin ? '#e5484d' : '#e2a04a')
          : '#8b8f98',
        borderColor: theme.border,
        borderWidth: 1,
      },
    }))
    const links = tree.links.map(l => ({ source: l.source, target: l.target }))
    // ECharts tree 需要单根；用虚拟根挂所有 size=1 节点
    const roots = data.map((d, i) => ({ ...d, _idx: i })).filter(d => d.name === '第1层')
    const forest: any[] = roots.length === 1
      ? [buildTree(roots[0], links, data)]
      : [{ name: '起点', value: baselineWin, children: roots.map(r => buildTree(r, links, data)),
           itemStyle: { color: theme.grid }, label: { show: false }, symbolSize: 6 }]

    const option: echarts.EChartsOption = {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBorder,
        textStyle: { color: theme.tooltipText, fontSize: 11 },
        formatter: (p: any) => {
          const d = p.data
          if (!d || d.terms == null) return '起点'
          return `<div style="font-weight:600;margin-bottom:3px">${d.terms.map((t: string) => t.startsWith('-') ? '非'+t.slice(2) : t.slice(1)).join(' + ')}</div>` +
            `胜率 ${pct1(d.win)} · 超额 ${sgn((d.win - baselineWin) * 100)}<br/>均值 ${sgn(d.mean)} · 笔数 ${d.n}<br/>OOS ${pct1(d.oos)}`
        },
      },
      series: [{
        type: 'tree',
        data: forest as any,
        orient: 'LR',
        left: '3%', right: '20%', top: '4%', bottom: '4%',
        symbol: 'circle',
        symbolSize: 10,
        label: {
          show: true, position: 'right', verticalAlign: 'middle',
          formatter: (p: any) => p.data.labelText ?? p.name,
          color: theme.text, fontSize: 9, lineHeight: 11,
        },
        leaves: { label: { position: 'right', verticalAlign: 'middle' } },
        lineStyle: { color: theme.grid, width: 1.2, curveness: 0.5 },
        expandAndCollapse: true,
        initialTreeDepth: 3,
        animationDuration: 300,
      }],
    }
    inst.current.setOption(option, true)

    const ro = new ResizeObserver(() => inst.current?.resize())
    ro.observe(box.current)
    return () => ro.disconnect()
  }, [tree, theme, baselineWin])

  return <div ref={box} className="h-[320px] w-full" />
}

/** 按 links 把扁平节点组装成树（按 target 找孩子） */
function buildTree(node: any, links: { source: number; target: number }[], data: any[]): any {
  const idx = data.indexOf(node)
  const children = links.filter(l => l.source === idx).map(l => buildTree(data[l.target], links, data))
  return { ...node, children: children.length ? children : undefined }
}

export function TagEvolution({ onCollapse }: { onCollapse: () => void }) {
  const [maxSize, setMaxSize] = useState(3)
  const [minSamples, setMinSamples] = useState(200)
  const [ran, setRan] = useState(false)

  const q = useQuery({
    queryKey: ['daban', 'evolution', maxSize, minSamples],
    queryFn: () => api.dabanResearchEvolution({ max_size: maxSize, min_samples: minSamples }),
    enabled: ran,
    staleTime: 5 * 60_000,
  })
  const resp = q.data
  const baseWin = resp?.meta.baseline.win ?? 0

  const best = useMemo(() => {
    if (!resp) return null
    let top = resp.layers.flatMap(l => l.top)
    top.sort((a, b) => b.stat.win - a.stat.win)
    return top[0] ?? null
  }, [resp])

  return (
    <div className="border border-border bg-surface/40">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-1.5">
        <Dna size={14} className="text-accent" />
        <span className="text-xs font-semibold text-foreground">标签进化</span>
        <span className="text-[10px] text-muted">事前形态的正向/反向 AND 组合，逐层找高胜率公式，带 IS/OOS 防过拟合</span>
        <label className="ml-2 text-[10px] text-muted">层数
          <select value={maxSize} onChange={e => setMaxSize(Number(e.target.value))}
            className="ml-1 h-6 rounded-btn bg-elevated px-1.5 text-[11px] text-foreground">
            <option value={2}>2 层</option><option value={3}>3 层</option>
          </select>
        </label>
        <label className="text-[10px] text-muted">最少笔数
          <select value={minSamples} onChange={e => setMinSamples(Number(e.target.value))}
            className="ml-1 h-6 rounded-btn bg-elevated px-1.5 text-[11px] text-foreground">
            <option value={100}>100</option><option value={200}>200</option><option value={500}>500</option>
          </select>
        </label>
        <button type="button" onClick={() => setRan(true)} disabled={q.isFetching}
          className="flex h-6 items-center gap-1 rounded-btn bg-accent/15 px-2 text-[11px] text-accent hover:bg-accent/25 disabled:opacity-50">
          {q.isFetching ? <Loader2 size={12} className="animate-spin" /> : null}
          {q.isFetching ? '搜索中…' : (ran ? '重新搜索' : '运行搜索')}
        </button>
        <button type="button" onClick={onCollapse} className="ml-auto text-[11px] text-muted hover:text-foreground">收起 ▲</button>
      </div>

      {!ran ? (
        <div className="px-3 py-6 text-center text-[11px] text-muted">
          点「运行搜索」开始：从 {144} 个事前形态文字（72 标签 × 正/反）逐层组合，约 4 秒。
        </div>
      ) : q.isLoading ? (
        <div className="px-3 py-6 text-center text-[11px] text-muted">正在枚举组合并计算 IS/OOS…</div>
      ) : q.isError ? (
        <div className="px-3 py-6 text-center text-[11px] text-danger">搜索失败：{String(q.error)}</div>
      ) : resp ? (
        <div className="space-y-3 p-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded bg-elevated/40 px-3 py-1.5 text-[11px]">
            <span className="text-muted">基准胜率 <b className="text-foreground">{pct1(baseWin)}</b> · 均值 <b className="text-foreground">{sgn(resp.meta.baseline.mean)}</b></span>
            <span className="text-muted">仅封板基准 <b className="text-foreground">{pct1(resp.meta.baseline_seal.win)}</b></span>
            <span className="text-muted">样本 <b className="text-foreground">{resp.meta.valid_events.toLocaleString()}</b> 笔</span>
            {best && <span className="text-muted">最优 <b className="text-bull">{pct1(best.stat.win)}</b>（{best.key.slice(0, 40)}）</span>}
            <span className="text-muted">耗时 {(resp.meta.elapsed_ms / 1000).toFixed(1)}s</span>
          </div>

          <div className="flex gap-3 overflow-x-auto">
            {resp.layers.map(l => <LayerTable key={l.size} layer={l} baselineWin={baseWin} />)}
          </div>

          <div>
            <div className="mb-1 flex items-center gap-3 text-[11px] text-secondary">
              <span className="font-semibold">进化路径树</span>
              <span className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-full bg-[#e5484d]" />胜率高且 OOS 稳</span>
              <span className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-full bg-[#e2a04a]" />胜率高但 OOS 退化</span>
              <span className="flex items-center gap-1"><i className="inline-block h-2 w-2 rounded-full bg-[#8b8f98]" />不及基准</span>
              <span className="text-[10px] text-muted">点节点可折叠/展开，滚轮缩放</span>
            </div>
            <div className="rounded border border-border"><TreeChart tree={resp.tree} baselineWin={baseWin} /></div>
          </div>

          <div className="text-[10px] leading-relaxed text-muted">
            {resp.meta.note} 紫色=反向标签(NOT)；胜率基于涨停后 r_t1 历史复盘，含未来数据，仅用于筛选题材与形态，不构成实盘信号。
          </div>
        </div>
      ) : null}
    </div>
  )
}
