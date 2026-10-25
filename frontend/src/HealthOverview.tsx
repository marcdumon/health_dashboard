import { useQuery } from '@tanstack/react-query'

import { getHealthOverview, type OverviewTile } from './api'

// the overview row of the Health page: one tile for each key value of the page's day (the weight, the blood pressure, the
// resting HR, ln RMSSD, the sleep and the activity), each with its value, its change against the 30-day mean, a sparkline
// of 30 days and its status; a tile without data says what to do. The server calculates; this file draws

// the tone of a status: within the usual range or met is calm, outside it asks for attention, a direction is plain
const TONE: Record<string, string> = {
  within: 'calm', met: 'calm', steady: 'calm', above: 'notice', below: 'notice', falling: 'plain', rising: 'plain', open: 'plain',
}
const SPARK_W = 120
const SPARK_H = 30
const SPARK_PAD = 3

// a change with its sign, the minus as a true minus sign
const signed = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : '±'}${Math.abs(v)}`
const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)

// the values of 30 days as a line over a light area, its last value as a dot; days without a value are bridged
function Spark({ values }: { values: (number | null)[] }) {
  const found = values.flatMap((v, i) => (v === null ? [] : [{ v, i }]))
  if (found.length < 2) return null
  const lo = Math.min(...found.map(p => p.v))
  const hi = Math.max(...found.map(p => p.v))
  const x = (i: number) => (i / Math.max(1, values.length - 1)) * (SPARK_W - 2 * SPARK_PAD) + SPARK_PAD
  const y = (v: number) => (hi === lo ? SPARK_H / 2 : SPARK_PAD + (1 - (v - lo) / (hi - lo)) * (SPARK_H - 2 * SPARK_PAD))
  const line = found.map((p, n) => `${n ? 'L' : 'M'}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join('')
  const end = found[found.length - 1]
  const area = `${line}L${x(end.i).toFixed(1)},${SPARK_H}L${x(found[0].i).toFixed(1)},${SPARK_H}Z`
  return (
    <svg className="spark" viewBox={`0 0 ${SPARK_W} ${SPARK_H}`} aria-hidden="true">
      <path d={area} className="spark-area" />
      <path d={line} className="spark-line" />
      <circle cx={x(end.i)} cy={y(end.v)} r={2.5} className="spark-end" />
    </svg>
  )
}

function Tile({ tile }: { tile: OverviewTile }) {
  return (
    <article className="overview-tile">
      <h3>{tile.label}</h3>
      {tile.text ? (
        <>
          <p className="overview-value">{tile.text}{tile.unit && <small> {tile.unit}</small>}</p>
          {tile.change && (
            <p className="overview-change">{signed(tile.change.value)}{tile.change.unit && ` ${tile.change.unit}`} against your
              30-day mean</p>
          )}
          <Spark values={tile.spark} />
          {tile.status_text && <p className={`overview-status ${TONE[tile.status ?? ''] ?? 'plain'}`}>{capital(tile.status_text)}</p>}
          {tile.detail && <p className="overview-detail">{tile.detail}</p>}
        </>
      ) : <p className="overview-empty">{tile.empty}</p>}
    </article>
  )
}

export function HealthOverview({ day }: { day: string }) {
  const found = useQuery({ queryKey: ['health-overview', day], queryFn: () => getHealthOverview(day),
                           placeholderData: previous => previous })
  if (found.isError) return <p className="cap-error">{found.error.message}</p>
  if (!found.data) return null
  return (
    <section className="health-overview" aria-label="Overview">
      {found.data.tiles.map(tile => <Tile key={tile.key} tile={tile} />)}
    </section>
  )
}
