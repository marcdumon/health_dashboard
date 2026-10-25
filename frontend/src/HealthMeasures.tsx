import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { getDayMeasurements, hideMeasurement, type HealthMeasurement } from './api'

// the Withings measurements of one day, each with Remove: in the history table and in the large view of a chart

export const whole = (v: number | null) => (v === null ? '—' : String(Math.round(v)))

// what a measurement held, in short: a weigh-in's main values, or a blood pressure reading
export function summary(m: HealthMeasurement): string {
  const v = m.values
  const of = (key: string) => whole(v[key] ?? null)
  if (m.kind === 'pressure') return `blood pressure ${of('systolic')}/${of('diastolic')} mmHg, pulse ${of('pulse')}`
  const parts: [string, string, number][] = [['weight', 'kg', 1], ['fat_ratio', '% fat', 1], ['muscle_mass', 'kg muscle', 1],
    ['visceral_fat', 'visceral fat', 1], ['hydration', 'kg water', 1], ['heart_rate', 'bpm', 0]]
  const said = parts.filter(([key]) => typeof v[key] === 'number').map(([key, unit, decimals]) => (
    key === 'visceral_fat' ? `${unit} ${v[key].toFixed(decimals)}` : `${v[key].toFixed(decimals)} ${unit}`))
  return `scale: ${said.join(', ') || 'no main values'}`
}

export function useForget() {
  const client = useQueryClient()
  return () => {
    client.invalidateQueries({ queryKey: ['health-history'] })
    client.invalidateQueries({ queryKey: ['health-overview'] })
    client.invalidateQueries({ queryKey: ['sleep-summary'] })
    client.invalidateQueries({ queryKey: ['health-day'] })
    client.invalidateQueries({ queryKey: ['health'] })
    client.invalidateQueries({ queryKey: ['health-analysis'] })
    client.invalidateQueries({ queryKey: ['health-relation'] })
    client.invalidateQueries({ queryKey: ['polar-sessions'] })
    client.invalidateQueries({ queryKey: ['polar-day'] })
    client.invalidateQueries({ queryKey: ['polar-detail'] })
    client.invalidateQueries({ queryKey: ['polar-activities'] })
    client.invalidateQueries({ queryKey: ['polar-waiting'] })
    client.invalidateQueries({ queryKey: ['polar-weeks'] })
    client.invalidateQueries({ queryKey: ['polar-zones'] })
    client.invalidateQueries({ queryKey: ['polar-energy'] })
    client.invalidateQueries({ queryKey: ['polar-activity'] })
  }
}

// each weigh-in and each blood pressure reading of the day, each with Remove: a wrong weigh-in goes, the good
// readings of that day stay
export function DayMeasurements({ day }: { day: string }) {
  const forget = useForget()
  const found = useQuery({ queryKey: ['health-day', day], queryFn: () => getDayMeasurements(day) })
  const remove = useMutation({ mutationFn: (grpids: number[]) => hideMeasurement(grpids), onSuccess: forget })
  if (found.isError) return <p className="cap-error">{found.error.message}</p>
  if (!found.data) return <p className="muted">Loading the measurements of this day…</p>
  const list = found.data.measurements
  return (
    <div className="health-day-measures">
      {list.length === 0 ? <p className="muted">No Withings measurements on this day.</p> : (
        <ul>
          {list.map(m => (
            <li key={m.grpids.join('-')}>
              <span className="health-measure-time">{m.measured_at.slice(11, 16)}</span>
              <span>{summary(m)}</span>
              <button type="button" className="quiet" disabled={remove.isPending}
                      onClick={() => remove.mutate(m.grpids)}>Remove</button>
            </li>
          ))}
        </ul>
      )}
      {remove.isError && <p className="cap-error">{remove.error.message}</p>}
    </div>
  )
}
