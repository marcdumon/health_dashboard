import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { getSupplementRelations } from './api'
import { link, Scatter } from './HealthAnalysis'
import { RELATION_CHOICES } from './healthValues'

// the Supplements tab of the history: a value of the health chosen here, and for each supplement taken in the period a
// scatter plot of the amount taken against that value, by day. The delay pairs the amount of a day with the value of a
// later day: a reading in the morning comes before the supplements of that day, so one day by default
const VALUE_KEY = 'health-supplements-value'
const DELAYS = [0, 1, 2, 3, 7]

export function SupplementHistory({ days, fetched }: { days: number; fetched: string | null }) {
  const [value, setValue] = useState(() => {
    let saved = ''
    try { saved = localStorage.getItem(VALUE_KEY) ?? '' } catch { /* no storage: the default value */ }
    return RELATION_CHOICES.some(([k]) => k === saved) ? saved : 'systolic'
  })
  const choose = (next: string) => {
    setValue(next)
    try { localStorage.setItem(VALUE_KEY, next) } catch { /* no storage: the choice lasts this visit */ }
  }
  const [lag, setLag] = useState(1)
  const found = useQuery({ queryKey: ['health-supplement-relations', value, days, lag, fetched],
                           queryFn: () => getSupplementRelations(value, days, lag), placeholderData: previous => previous })
  const [, label, unit, decimals] = RELATION_CHOICES.find(([k]) => k === value) ?? RELATION_CHOICES[0]
  const y = { label, unit, decimals }
  const relations = found.data?.relations ?? []
  return (
    <div className="health-charts">
      <div className="analysis-against-row">
        <label className="analysis-against">Value
          <select value={value} onChange={e => choose(e.target.value)}>
            {RELATION_CHOICES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
        <label className="analysis-against">Amount taken
          <select value={lag} onChange={e => setLag(Number(e.target.value))}>
            {DELAYS.map(n => (
              <option key={n} value={n}>{n === 0 ? 'on the same day' : n === 1 ? 'the day before' : `${n} days before`}</option>
            ))}
          </select>
        </label>
      </div>
      {found.isError && <p className="cap-error">{found.error.message}</p>}
      {found.data && !relations.length && <p className="stats-empty">No supplements taken in these days.</p>}
      <div className="supplement-relations">
        {relations.map(r => {
          const x = { label: `${r.name} taken`, unit: r.unit, decimals: r.unit ? 1 : 0 }
          return (
            <figure key={r.other}>
              <figcaption>{r.name} <span className="muted">{r.unit ? `${r.unit} a day` : 'taken or not'}</span></figcaption>
              <p className="analysis-say muted">{link(r, y, x)}</p>
              {r.n >= 3 && <Scatter found={r} x={x} y={y} height={220} />}
            </figure>
          )
        })}
      </div>
    </div>
  )
}
