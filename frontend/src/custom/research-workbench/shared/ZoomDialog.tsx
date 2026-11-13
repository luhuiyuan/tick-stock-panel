import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, X } from 'lucide-react'
import { EChartsCandlestick } from '@/components/EChartsCandlestick'
import { EmptyState } from '@/components/EmptyState'
import { ErrorState } from '@/components/ErrorState'
import { useDialogBackdrop } from '@/lib/useDialogBackdrop'
import { useListNav } from '@/lib/useListNav'
import { buildZoomCaption } from './zoomCaption'
import type { ResearchCtx, ResearchRecord, ResearchRecipe } from './types'

/** 共用的放大框。三种研究只通过说明卡的 dialogDecor / frames / zoomNouns 表达差异。 */
export function ZoomDialog({
  recipe,
  rows,
  currentId,
  asOf,
  ctx,
  keyPrefix,
  onSelect,
  onClose,
}: {
  recipe: ResearchRecipe
  rows: ResearchRecord[]
  currentId: string
  asOf?: string
  ctx: ResearchCtx
  keyPrefix: readonly unknown[]
  onSelect: (id: string) => void
  onClose: () => void
}) {
  const backdrop = useDialogBackdrop(onClose)
  const nav = useListNav({
    items: rows,
    keyOf: rec => rec.id,
    currentKey: currentId,
    onNavigate: rec => onSelect(rec.id),
    wrapHints: { head: '已到本页第一条', tail: '已到本页最后一条' },
  })
  const row = rows.find(item => item.id === currentId) ?? null
  const [frame, setFrame] = useState('span')
  useEffect(() => { setFrame('span') }, [currentId])

  const zoom = useQuery({
    queryKey: [...keyPrefix, 'zoom', currentId],
    enabled: row != null,
    queryFn: () => recipe.fetchZoom(row!),
  })

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const decor = useMemo(
    () => (row ? recipe.dialogDecor(row, ctx, frame) : null),
    [row, ctx, frame, recipe],
  )

  if (!row || !decor) return null
  const bars = zoom.data?.bars ?? []
  const data = bars.map(bar => ({
    date: bar.date,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume ?? 0,
  }))
  const caption = zoom.isLoading
    ? '正在读取前后一年的 K 线…'
    : zoom.isError
      ? '前后一年的 K 线没有读出来。'
      : buildZoomCaption({
          bars,
          fromDate: zoom.data?.fromDate ?? '',
          toDate: zoom.data?.toDate ?? '',
          asOf,
          barsBefore: zoom.data?.barsBefore ?? 0,
          barsAfter: zoom.data?.barsAfter ?? 0,
          frame,
          nouns: recipe.zoomNouns,
          frames: recipe.frames,
        })

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" onMouseDown={backdrop.onMouseDown} onClick={backdrop.onClick}>
      <div className="flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-xl" onMouseDown={event => event.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-foreground">{row.name || row.symbol} · {row.symbol}</div>
            <div className="mt-0.5 text-xs text-muted">{decor.subtitle} · {caption}</div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {recipe.frames && (
              <>
                <button type="button" className={`h-8 rounded-btn px-2 text-xs ${frame === 'span' ? 'bg-elevated text-foreground' : 'text-muted hover:text-foreground'}`} onClick={() => setFrame('span')}>{recipe.frames.spanLabel}</button>
                <button type="button" className={`h-8 rounded-btn px-2 text-xs ${frame === 'year' ? 'bg-elevated text-foreground' : 'text-muted hover:text-foreground'}`} onClick={() => setFrame('year')}>{recipe.frames.allLabel}</button>
              </>
            )}
            <button type="button" className="inline-flex h-8 items-center gap-1 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => nav.go(-1)} disabled={!nav.navEnabled}>
              <ChevronLeft className="h-3.5 w-3.5" />上一条
            </button>
            <button type="button" className="inline-flex h-8 items-center gap-1 rounded-btn bg-elevated px-2 text-xs text-secondary hover:text-foreground" onClick={() => nav.go(1)} disabled={!nav.navEnabled}>
              下一条<ChevronRight className="h-3.5 w-3.5" />
            </button>
            <button type="button" className="grid h-8 w-8 place-items-center rounded-btn text-muted hover:bg-elevated hover:text-foreground" onClick={onClose} aria-label="关闭">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="min-h-0 overflow-auto p-3">
          {zoom.isLoading ? (
            <div className="grid h-[520px] place-items-center text-sm text-muted">正在读取前后一年的 K 线…</div>
          ) : zoom.isError ? (
            <ErrorState hint="前后一年的日线没有读出来。" onRetry={() => zoom.refetch()} retrying={zoom.isRefetching} />
          ) : data.length === 0 ? (
            <EmptyState title="没有可放大的 K 线" hint="这只股票在这一段前后一年的日线没有读到。" />
          ) : (
            <EChartsCandlestick
              key={`${row.id}|${frame}|${decor.chartKey ?? ''}`}
              data={data}
              height={520}
              visibleBars="all"
              focus={decor.focus ?? 'all'}
              showMA
              ranges={decor.ranges}
              markers={decor.markers}
              stockInfo={{ name: row.name }}
              symbol={row.symbol}
            />
          )}
          {nav.wrapMsg && <div className="mt-2 text-center text-xs text-muted">{nav.wrapMsg}</div>}
        </div>
      </div>
    </div>
  )
}
