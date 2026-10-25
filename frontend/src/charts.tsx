import { useEffect, useRef, useState } from 'react'

// the small parts every chart uses: its width, a round maximum, the tooltip and the legend

// the chart's drawing width follows its box, so text never stretches
export function useWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(600)
  useEffect(() => {
    if (!ref.current) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.round(entry.contentRect.width))))
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}

export function niceMax(value: number): number {
  if (value <= 5) return 5
  const step = 10 ** Math.floor(Math.log10(value))
  return Math.ceil(value / step) * step
}

export type Tip = { x: number; y: number; title: string; rows: { key: string; value: string; kind: string }[] }

export function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null
  return (
    <div className="chart-tip" style={{ left: tip.x, top: tip.y }} role="status">
      <strong>{tip.title}</strong>
      {tip.rows.map(r => <span key={r.key}><i className={`key ${r.kind}`} /><b>{r.value}</b> {r.key}</span>)}
    </div>
  )
}

export function Legend({ items }: { items: { label: string; kind: string; line?: boolean }[] }) {
  return (
    <ul className="chart-legend">
      {items.map(i => <li key={i.label}><i className={`key ${i.kind}${i.line ? ' line' : ''}`} />{i.label}</li>)}
    </ul>
  )
}
