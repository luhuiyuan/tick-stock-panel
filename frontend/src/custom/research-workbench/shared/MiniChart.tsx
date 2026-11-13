import type { ResearchBar, MiniSpec } from './types'

const BULL = '#C74040'
const BEAR = '#2D9B65'
const AFTER = 'rgba(45,155,101,0.16)'
const WIDTH = 280
const HEIGHT = 96

/** 共用的缩略 K 线。说明卡只给高亮配置(MiniSpec), 不自己画蜡烛。 */
export function MiniChart({ bars, spec }: { bars: ResearchBar[]; spec: MiniSpec }) {
  if (bars.length === 0) {
    return <div className="grid h-24 place-items-center text-xs text-muted">这段没有可画的 K 线</div>
  }
  let hi = -Infinity
  let lo = Infinity
  for (const bar of bars) {
    if (bar.high > hi) hi = bar.high
    if (bar.low < lo) lo = bar.low
  }
  const range = hi - lo || 1
  const barW = WIDTH / bars.length
  const y = (value: number) => 4 + (1 - (value - lo) / range) * (HEIGHT - 8)

  const idxOf = (date: string, fromEnd = false) => {
    if (fromEnd) {
      for (let i = bars.length - 1; i >= 0; i -= 1) if (bars[i].date <= date) return i
      return -1
    }
    return bars.findIndex(bar => bar.date >= date)
  }

  const startIdx = spec.band ? idxOf(spec.band.start) : -1
  const endIdx = spec.band ? idxOf(spec.band.end, true) : -1
  const band = startIdx >= 0 && endIdx >= startIdx
    ? { x: startIdx * barW, width: (endIdx - startIdx + 1) * barW }
    : null
  const afterIdx = spec.shadeAfter
    ? (spec.shadeAfterInclusive
        ? idxOf(spec.shadeAfter)
        : bars.findIndex(bar => bar.date > spec.shadeAfter!))
    : -1
  const afterBand = afterIdx >= 0
    ? { x: afterIdx * barW, width: (WIDTH - afterIdx * barW) }
    : null

  return (
    <svg width="100%" height={HEIGHT} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="block" role="img" aria-label="研究区间缩略K线">
      {afterBand && <rect x={afterBand.x} y={0} width={afterBand.width} height={HEIGHT} fill={AFTER} />}
      {band && <rect x={band.x} y={0} width={band.width} height={HEIGHT} fill="rgba(199,64,64,0.14)" />}
      {bars.map((bar, index) => {
        const color = bar.close >= bar.open ? BULL : BEAR
        const cx = index * barW + barW / 2
        const bodyW = Math.max(barW * 0.55, 1.5)
        const top = y(Math.max(bar.open, bar.close))
        const bottom = y(Math.min(bar.open, bar.close))
        return (
          <g key={bar.date}>
            <line x1={cx} x2={cx} y1={y(bar.high)} y2={y(bar.low)} stroke={color} strokeWidth={1} />
            <rect x={cx - bodyW / 2} y={top} width={bodyW} height={Math.max(bottom - top, 1)} fill={color} />
          </g>
        )
      })}
      {(spec.vlines ?? []).map((line, i) => {
        const idx = idxOf(line.date)
        if (idx < 0) return null
        const x = idx * barW + barW / 2
        return <line key={`${line.date}-${i}`} x1={x} x2={x} y1={0} y2={HEIGHT} stroke={line.color} strokeWidth={1.5} strokeDasharray={line.dash ?? '3 2'} />
      })}
    </svg>
  )
}
