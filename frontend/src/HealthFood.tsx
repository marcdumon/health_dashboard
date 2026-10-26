import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { clearFood, setFood, type FoodDay, type HealthDayRow, type HealthView } from './api'
import { TimeField } from './HealthTime'

// the food of a day, noted without counting: scales from -3 to +3 against the usual day, marks, and on a fast day its
// kcal and grams in place of the scales
const SCALES: [keyof FoodDay, string][] = [['amount', 'Amount'], ['protein', 'Protein'], ['carbs', 'Carbs'], ['fat', 'Fat']]
const STEPS = [-3, -2, -1, 0, 1, 2, 3]
const MARKS: [keyof FoodDay, string][] = [['fast', 'Fast day'], ['alcohol', 'Alcohol'], ['salt', 'Much salt']]
const FAST_FIELDS: [keyof FoodDay, string, string][] = [
  ['kcal', 'Energy', 'kcal'], ['protein_g', 'Protein', 'g'], ['carbs_g', 'Carbs', 'g'], ['fat_g', 'Fat', 'g'],
]
const signed = (n: number) => (n > 0 ? `+${n}` : n < 0 ? `−${-n}` : '0')

// the Food part of the Today card: the day before and the day, one above the other, as both are noted in the morning
// (the last food and the scales of the day before, the first food of the day)
export function Food({ data, before, labels, onChange }: {
  data: HealthView; before: string; labels: [string, string]; onChange: (v: HealthView) => void
}) {
  return (
    <section className="health-angina health-food">
      <p className="muted health-hint">The scales: 0 = your usual day.</p>
      <FoodDayPart pageDay={data.day} day={before} label={labels[0]} food={data.food.day_before} onChange={onChange} />
      <FoodDayPart pageDay={data.day} day={data.day} label={labels[1]} food={data.food.day} onChange={onChange} />
    </section>
  )
}

function FoodDayPart({ pageDay, day, label, food, onChange }: {
  pageDay: string; day: string; label: string; food: FoodDay | null; onChange: (v: HealthView) => void
}) {
  const save = useMutation({ mutationFn: (changes: Partial<FoodDay>) => setFood(pageDay, day, changes), onSuccess: onChange })
  const clear = useMutation({ mutationFn: () => clearFood(pageDay, day), onSuccess: onChange })
  const error = save.error ?? clear.error
  const busy = save.isPending || clear.isPending
  const change = (key: keyof FoodDay, value: FoodDay[keyof FoodDay]) => save.mutate({ [key]: value } as Partial<FoodDay>)
  return (
    <div className="health-food-day">
      <div className="health-moment-head">
        <h3>{label}</h3>
        {food && <button type="button" className="quiet" disabled={busy} onClick={() => clear.mutate()}>Clear</button>}
      </div>
      <div className="health-food-times">
        {(['first_food', 'last_food'] as const).map(key => (
          <label key={key}>{key === 'first_food' ? 'First food' : 'Last food'}
            {/* a refused time goes back to the stored one: the field starts again from it, the error says why */}
            <TimeField key={`${day}-${key}-${food?.[key] ?? ''}-${save.isError}`} value={food?.[key] ?? ''}
                       label={`${key === 'first_food' ? 'First' : 'Last'} food, ${label}`} onSave={at => change(key, at)} />
          </label>
        ))}
      </div>
      <div className="health-scores" role="group" aria-label={`Marks, ${label}`}>
        {MARKS.map(([key, text]) => (
          <button key={key} type="button" className={`choice-chip${food?.[key] ? ' chosen' : ''}`} aria-pressed={Boolean(food?.[key])}
                  disabled={busy} onClick={() => change(key, !food?.[key])}>{text}</button>
        ))}
      </div>
      {food?.fast ? (
        <div className="health-tape-grid">
          {FAST_FIELDS.map(([key, text, unit]) => (
            <FastField key={`${day}-${key}`} label={text} unit={unit} value={food[key] as number | null}
                       onSave={value => change(key, value)} />
          ))}
        </div>
      ) : SCALES.map(([key, text]) => {
        const chosen = food?.[key] as number | null | undefined
        return (
          <div key={key} className="health-scores health-food-scale" role="group" aria-label={`${text} against your usual day, ${label}`}>
            <span className="health-food-label">{text}</span>
            {STEPS.map(n => (
              // on a noted day a scale not chosen counts as 0: that 0 shows, lighter. Clicking the chosen step again clears it
              <button key={n} type="button" aria-pressed={chosen === n} disabled={busy}
                      className={`choice-chip${chosen === n ? ' chosen' : food && chosen === null && n === 0 ? ' counted' : ''}`}
                      onClick={() => change(key, chosen === n ? null : n)}>{signed(n)}</button>
            ))}
          </div>
        )
      })}
      {error && <p className="cap-error">{error.message}</p>}
    </div>
  )
}

// a value of a fast day: saves when you leave it; an empty field removes it
function FastField({ label, unit, value, onSave }: {
  label: string; unit: string; value: number | null; onSave: (value: number | null) => void
}) {
  const shown = value === null ? '' : String(value)
  const [draft, setDraft] = useState(shown)
  useEffect(() => setDraft(shown), [shown])
  const leave = () => {
    const text = draft.trim()
    const number = text === '' ? null : Number(text)
    if (number !== null && Number.isNaN(number)) setDraft(shown)
    else if (number !== value) onSave(number)
  }
  return (
    <label className="health-tape-field">{label} ({unit})
      <input type="number" inputMode="decimal" min={0} value={draft} aria-label={`${label} of the fast day in ${unit}`}
             onChange={e => setDraft(e.target.value)} onBlur={leave} />
    </label>
  )
}

// the line of the closed Food part: what each of the two days holds
export function foodSummary(data: HealthView, labels: [string, string]): string {
  const noted = (food: FoodDay | null) => {
    if (!food) return 'not noted'
    if (food.fast) return food.kcal === null ? 'fast day' : `fast day, ${food.kcal} kcal`
    return food.first_food && food.last_food ? `${food.first_food}–${food.last_food}` : 'noted'
  }
  return `${labels[0]}: ${noted(data.food.day_before)}; ${labels[1]}: ${noted(data.food.day)}`
}

// the food of a day of the history in one line, for the day opened in the table; null on a day without a note
export function foodLine(row: HealthDayRow): string | null {
  const value = (key: string) => {
    const found = row[key]
    return typeof found === 'number' ? found : null
  }
  const fastDay = value('food:fast_day')
  if (fastDay === null) return null
  const kcal = value('food:kcal')
  const scales = SCALES.flatMap(([key, text]) => {
    const n = value(`food:${key}`)
    return n ? [`${text.toLowerCase()} ${signed(n)}`] : []
  })
  const window = value('food:window')
  const parts = [
    fastDay > 0 ? `fast day ${fastDay}${kcal === null ? '' : `, ${kcal} kcal`}` : scales.join(', ') || 'a usual day',
    value('food:alcohol') ? 'alcohol' : '', value('food:salt') ? 'much salt' : '',
    window === null ? '' : `eating window ${window} h`,
  ]
  return `Food: ${parts.filter(Boolean).join('; ')}`
}
