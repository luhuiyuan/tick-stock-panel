import {useSearchParams} from 'react-router-dom'
import {CoverageCaseCard, coverageLabels as labels} from './CoverageCaseCard'
import {useState} from 'react'
import {useQuery,useMutation} from '@tanstack/react-query'
import {api, type CoverageSummary} from '@/lib/api'
import {StockPreviewDialog} from '@/components/StockPreviewDialog'
import {coveragePreview} from './coveragePreview'
const keys=['hit','miss','unknown'] as const
const btn='rounded-btn bg-elevated px-3 py-2 text-sm disabled:opacity-45'
const pct=(n:number,d:number)=>d?`${(100*n/d).toFixed(2)}%`:'—'
type Group=typeof keys[number]
const stateNames={ready:'已计算',pending:'待计算',unavailable:'不可评估',stale:'需更新',failed:'计算失败'}
const columns=[['coverage','提前覆盖率'],['hit','已覆盖'],['miss','未覆盖'],['unknown','未知'],['frequency','日常触发率']] as const
type Metric=typeof columns[number][0]
function metric(r:CoverageSummary,k:Metric){return k==='coverage'?r.hit/(r.total||1):k==='frequency'?r.fired/(r.evaluable||1):r[k]}
function pageNumber(value:string|null,max:number){return Math.min(max,Math.max(1,Math.trunc(Number(value)||1)))}
function Pager({page,total,size,change}:{page:number;total:number;size:number;change:(p:number)=>void}){
  const pages=Math.max(1,Math.ceil(total/size))
  return <nav aria-label="分页" className="flex items-center justify-between border-t border-border py-2 text-xs text-muted"><span>共 {total.toLocaleString()} 条 · 每页 {size} 条</span><div><button className={btn} disabled={page===1} onClick={()=>change(page-1)}>上一页</button>　{page}/{pages}　<button className={btn} disabled={page===pages} onClick={()=>change(page+1)}>下一页</button></div></nav>
}
function useViewParams(){
  const [params,setParams]=useSearchParams()
  function change(patch:Record<string,string|null>,replace=true){const next=new URLSearchParams(params);for(const [k,v] of Object.entries(patch)){if(v===null)next.delete(k);else next.set(k,v)}setParams(next,{replace})}
  return {params,change}
}
function Details({r}:{r:CoverageSummary}){
  const {params,change}=useViewParams(), [id,setId]=useState<string|null>(null)
  const group=keys.includes(params.get('group') as Group)?params.get('group') as Group:'hit'
  const page=pageNumber(params.get('eventPage'),Math.max(1,Math.ceil(r[group]/24)))
  const q=useQuery({queryKey:['coverage-thumbnails',r.signal_id,r.version,group,page],queryFn:()=>api.surgeCoverageEvents(r.signal_id,r.version,group,(page-1)*24)})
  const rows=q.data?.rows??[]
  const selected=rows.find(e=>e.id===id)
  return <section className="min-h-0 flex-1 space-y-3 overflow-auto p-4"><button className={btn} onClick={()=>change({detail:null,group:null,eventPage:null})}>← 返回信号覆盖榜</button><h2 className="text-lg font-semibold">{r.name} · 猛拉明细</h2>{r.limit_policy&&<p className="text-xs text-muted">{r.limit_policy}</p>}<p className="text-xs text-muted">{r.stale?'旧结果 · ':''}计算于 {new Date(r.computed_at).toLocaleString()}</p><div>{keys.map(k=><button key={k} aria-pressed={group===k} className={btn+' mr-1 aria-pressed:text-accent'} onClick={()=>change({group:k,eventPage:null})}>{labels[k]} {r[k].toLocaleString()}</button>)}</div><p className="text-xs text-muted">底色：猛拉段　黄线：提前触发日　·　点击缩略图放大，缺数据不补画</p>
  {q.isError?<p role="alert">{String(q.error)}</p>:q.isLoading?<p>正在读取本页 K 线…</p>:!rows.length?<p>该组没有记录</p>:<div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{rows.map(row=><CoverageCaseCard key={row.id} row={row} summary={r} onOpen={setId}/>)}</div>}
  <Pager total={r[group]} size={24} page={page} change={p=>change({eventPage:String(p)})}/>{selected&&<StockPreviewDialog key={selected.id} symbol={selected.symbol} name={selected.name}
    navList={rows.map(e=>({caseId:e.id,symbol:e.symbol,name:e.name}))}
    historicalCase={{...coveragePreview(selected,r.name),id:selected.id,onSelect:setId}} onClose={()=>setId(null)}/>}</section>
}
export function SurgeCoverage(){
  const [confirm,setConfirm]=useState<string|null>(null),{params,change}=useViewParams()
  const status=useQuery({queryKey:['surge-coverage-status'],queryFn:api.surgeCoverage,refetchInterval:q=>['pending','running'].includes(q.state.data?.job?.status??'')?1500:15000})
  const start=useMutation({mutationFn:api.calculateSurgeCoverage,onSuccess:()=>{setConfirm(null);void status.refetch()}})
  const entries=status.data?.signals??[],job=status.data?.job,busy=['running','pending'].includes(job?.status??'')||start.isPending
  const sort=columns.find(([k])=>params.get('rankSort')===k)?.[0]??'coverage',asc=params.get('rankOrder')==='asc'
  const items=[...entries].sort((a,b)=>{if(a.status==='ready'&&a.result&&b.status==='ready'&&b.result)return (metric(a.result,sort)-metric(b.result,sort))*(asc?1:-1)||a.signal_id.localeCompare(b.signal_id);return Number(b.status==='ready')-Number(a.status==='ready')||a.signal_id.localeCompare(b.signal_id)}),page=pageNumber(params.get('rankPage'),Math.max(1,Math.ceil(items.length/20)))
  const r=items.find(e=>e.status==='ready')?.result, detail=items.find(e=>e.signal_id===params.get('detail'))?.result
  if(detail)return <Details key={detail.signal_id+detail.version} r={detail}/>
  return <section className="min-h-0 flex-1 space-y-3 overflow-auto p-4"><div className="flex items-center justify-between"><h2 className="text-lg font-semibold">信号覆盖榜</h2><button disabled={busy||!status.data} className={btn} onClick={()=>setConfirm('*')}>计算 / 续算全部</button></div>
  <p className="text-xs text-muted">启动前 5 个交易日 · 2025-10-27 — 2026-11-05　{r&&`${r.stale?'基于旧快照 · ':''}共同样本 ${r.total.toLocaleString()} 段 · 快照截至 ${r.as_of}`}</p>
  {status.data&&<p className="text-xs text-muted">共 {entries.length} 个信号 · {entries.filter(e=>e.status==='ready').length} 个可排名 · {entries.filter(e=>e.status==='unavailable').length} 个不可评估</p>}
  {confirm&&<div className="rounded border border-border p-3"><p>{confirm==='*'?'计算未完成或需更新的信号；跳过当前已完成项。':`重新计算「${items.find(e=>e.signal_id===confirm)?.name}」。`}不重建猛拉快照。</p><button className={btn} disabled={busy} onClick={()=>start.mutate(confirm==='*'?undefined:confirm)}>确认开始计算</button> <button className={btn} onClick={()=>setConfirm(null)}>取消</button></div>}{busy&&<p role="status">正在排队或计算 · {job?.progress??0}%</p>}{(status.isError||start.isError||job?.status==='failed')&&<p role="alert">{String(status.error??start.error??job?.error??'任务失败')}</p>}
  <div className="overflow-x-auto"><table className="w-full whitespace-nowrap text-sm tabular-nums"><caption className="sr-only">已计算信号覆盖率排名</caption><thead className="bg-elevated text-xs text-muted"><tr><th className="p-2 text-left">信号名称</th>{columns.map(([k,name])=><th key={k} className="p-2 text-right" aria-sort={sort===k?(asc?'ascending':'descending'):'none'}><button onClick={()=>change({rankSort:k,rankOrder:sort===k&&!asc?'asc':'desc',rankPage:null})}>{name} {sort===k?(asc?'↑':'↓'):'↕'}</button></th>)}<th>操作</th></tr></thead><tbody>{items.slice((page-1)*20,page*20).map(entry=>{const item=entry.status==='ready'?entry.result:null;return <tr key={entry.signal_id} className="border-b border-border"><th className="p-2 text-left font-medium">{entry.name}{item?.limit_policy&&<div className="text-xs font-normal text-muted">{item.limit_policy}</div>}{!item&&<div className="text-xs font-normal text-muted">{stateNames[entry.status]} · {entry.reason}</div>}</th>{columns.map(([k])=><td key={k} className={'p-2 text-right '+(k==='coverage'?'text-accent':'')}>{!item?'—':k==='coverage'?pct(item.hit,item.total):k==='frequency'?pct(item.fired,item.evaluable):item[k].toLocaleString()}</td>)}<td className="px-2 text-center"><button disabled={!entry.result} className="text-accent disabled:opacity-45" onClick={()=>change({detail:entry.signal_id,group:null,eventPage:null},false)}>{!item&&entry.result?'上次明细':'查看明细'}</button> · <button disabled={busy} onClick={()=>setConfirm(entry.signal_id)}>计算</button></td></tr>})}</tbody></table></div>
  {!items.length&&<p>{status.isLoading?'正在读取…':'尚无成功结果'}</p>}<Pager page={page} size={20} total={items.length} change={p=>change({rankPage:String(p)})}/><p className="text-xs text-muted">覆盖率不是触发后的猛拉概率。无有效结果的信号列在榜尾，不参与数值排名。</p></section>
}
