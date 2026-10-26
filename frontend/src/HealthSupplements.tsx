import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type KeyboardEvent, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import {
  addIntake, addSupplement, changeSupplement, copyRegime, getRegime, removeIntake, removeSupplement, setIntakeAmount, takeMoment, undoMoment,
  type HealthSupplement, type HealthView, type Regime, type RegimeItem, type SupplementChange, type SupplementChangeFields,
} from './api'
import { closeOutside } from './dialog'
import { clock } from './HealthTime'

// the supplements of a day. Taken logs the plan of a moment at the time now, +1 logs one dose; each entry keeps its own
// amount, which can be changed, and an entry is removed at once. The changes of the card wait for each other: three quick
// clicks are three entries, and no answer overtakes a later one. Regime opens the list to change it: Save counts from the
// day of the page on, and the days before keep their plan
const SCOPE = { id: 'health-supplements' }
type Change = (v: HealthView) => void
const UNITS = ['mg', 'µg', 'g', 'IU', 'FU']

// an amount without trailing zeros
export const amount = (n: number) => String(+n.toFixed(2))
export const shortDay = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })

// what a change of the plan did to the amount a day
export const changeText = (c: SupplementChange) => {
  if (!c.unit) return c.after ? 'started' : 'stopped'
  if (!c.before) return `started, ${amount(c.after)} ${c.unit} a day`
  if (!c.after) return `stopped (was ${amount(c.before)} ${c.unit} a day)`
  return `${amount(c.before)} → ${amount(c.after)} ${c.unit} a day`
}

// the summary of the fold: the moments taken of those with a plan
export function supplementSummary(view: HealthView['supplements']): string {
  const planned = view.moments.filter(m => m.planned)
  if (!planned.length) return view.items.some(s => s.entries.length) ? 'taken without a plan' : 'nothing planned'
  return `${planned.filter(m => m.taken_at).length} of ${planned.length} moments taken`
}

export function Supplements({ data, onChange }: { data: HealthView; onChange: Change }) {
  const [regime, setRegime] = useState(false)
  const done = { onSuccess: onChange, scope: SCOPE }
  const take = useMutation({ mutationFn: (moment: string) => takeMoment(data.day, moment, clock()), ...done })
  const undo = useMutation({ mutationFn: (moment: string) => undoMoment(data.day, moment), ...done })
  const add = useMutation({ mutationFn: (id: number) => addIntake(data.day, id, clock()), ...done })
  const change = useMutation({ mutationFn: ({ id, value }: { id: number; value: number }) => setIntakeAmount(id, value), ...done })
  const remove = useMutation({ mutationFn: (id: number) => removeIntake(id), ...done })
  const error = take.error ?? undo.error ?? add.error ?? change.error ?? remove.error
  const { moments, items } = data.supplements
  return (
    <section className="health-supplements">
      <div className="health-moment-head">
        <p className="muted health-hint">
          {items.length ? 'Taken logs the plan of a moment, +1 one dose.' : 'No supplements yet. Add them in the regime.'}
        </p>
        <div className="health-head-actions">
          <button type="button" className="quiet" onClick={() => setRegime(true)}>Regime</button>
        </div>
      </div>
      {moments.some(m => m.planned) && (
        <div className="supplement-moments" role="group" aria-label="Moments of the day">
          {moments.filter(m => m.planned).map(m => (m.taken_at ? (
            <span key={m.name} className="supplement-moment done">
              {m.name} <span>{m.taken_at}</span>
              <button type="button" aria-label={`Remove the entries of ${m.name}`} title="Remove the entries of this moment"
                      onClick={() => undo.mutate(m.name)}>×</button>
            </span>
          ) : (
            <button key={m.name} type="button" className="supplement-moment" title={`Log the plan of ${m.name.toLowerCase()} now`}
                    onClick={() => take.mutate(m.name)}>{m.name}</button>
          )))}
        </div>
      )}
      <ul className="health-unit-list">
        {items.map(s => (
          <SupplementRow key={s.id} supplement={s} onAdd={() => add.mutate(s.id)}
                         onAmount={(id, value) => change.mutate({ id, value })} onRemove={id => remove.mutate(id)} />
        ))}
      </ul>
      {error && <p className="cap-error">{error.message}</p>}
      {regime && <RegimeDialog day={data.day} today={data.today} onClose={() => setRegime(false)} />}
    </section>
  )
}

// one supplement of the day: +1, the amount taken against the plan, and each entry with its time and amount
function SupplementRow({ supplement: s, onAdd, onAmount, onRemove }: {
  supplement: HealthSupplement; onAdd: () => void; onAmount: (id: number, value: number) => void; onRemove: (id: number) => void
}) {
  const yesNo = !s.dose_unit
  const one = `${amount(s.step ?? 0)} ${s.dose_unit}`
  const full = yesNo ? s.total > 0 : s.planned > 0 && s.total >= s.planned
  return (
    <li>
      {s.listed
        ? <button type="button" className="health-plus" disabled={yesNo && s.entries.length > 0}
                  aria-label={yesNo ? `${s.name}: taken now` : `${s.name}: one dose of ${one} now`}
                  title={yesNo ? 'Taken now' : `One dose: ${one}`} onClick={onAdd}>+1</button>
        : <span aria-hidden="true" />}
      <span className="health-supplement">
        <strong>{s.name}</strong> {s.comment && <span className="muted">{s.comment}</span>}
        {!s.listed && <span className="muted">removed from the list</span>}
      </span>
      <span className={`health-count${full ? ' done' : ''}`}>
        {yesNo ? (s.total ? 'taken' : 'not taken')
          : s.planned ? `${amount(s.total)} of ${amount(s.planned)} ${s.dose_unit}` : `${amount(s.total)} ${s.dose_unit}`}
      </span>
      {s.entries.length > 0 && (
        <span className="health-units">
          {s.entries.map(e => (
            <span key={e.id} className="health-unit">
              <span className="health-unit-time">{e.taken_at}</span>
              {!yesNo && <AmountField key={e.amount} value={e.amount} unit={s.dose_unit}
                                      label={`Amount of ${s.name} of ${e.taken_at}`} onSave={v => onAmount(e.id, v)} />}
              <button type="button" aria-label={`Remove the entry of ${s.name} of ${e.taken_at}`} title="Remove this entry"
                      onClick={() => onRemove(e.id)}>×</button>
            </span>
          ))}
        </span>
      )}
    </li>
  )
}

const enter = (e: KeyboardEvent<HTMLInputElement>) => { if (e.key === 'Enter') e.currentTarget.blur() }

// an amount changed in place: it keeps its draft and saves when the user leaves it or presses Enter
function AmountField({ value, unit, label, onSave }: { value: number; unit: string; label: string; onSave: (v: number) => void }) {
  const [draft, setDraft] = useState(amount(value))
  const save = () => {
    const n = Number(draft)
    if (draft.trim() && n > 0 && n !== value) onSave(n)
    else setDraft(amount(value))
  }
  return (
    <span className="health-amount">
      <input type="number" min={0} step="any" value={draft} aria-label={label} onChange={e => setDraft(e.target.value)}
             onBlur={save} onKeyDown={enter} />
      {unit}
    </span>
  )
}

// a number typed in a field: null when empty, undefined when not a number of 0 or more
const typed = (text: string): number | null | undefined => {
  if (!text.trim()) return null
  const n = Number(text)
  return Number.isFinite(n) && n >= 0 ? n : undefined
}
const EMPTY = { name: '', unit: 'mg', step: '', comment: '' }
// the columns of the plan stand apart from those of the supplement: a line before the first moment, a light background
const plan = (index: number) => (index === 0 ? 'regime-plan regime-plan-start' : 'regime-plan')

// a supplement of the regime as typed, before Save
type Draft = { name: string; unit: string; step: string; comment: string; plan: Record<string, string> }
const draftOf = (s: RegimeItem, moments: string[]): Draft => ({
  name: s.name, unit: s.dose_unit, step: s.step === null ? '' : amount(s.step), comment: s.comment,
  plan: Object.fromEntries(moments.map(m => [m, s.plan[m] ? amount(s.plan[m]) : ''])),
})
// the fields to send of a draft, or what is wrong with it
const fieldsOf = (d: Draft, moments: string[]): SupplementChangeFields | string => {
  if (!d.name.trim()) return 'give it a name'
  const one = d.unit ? typed(d.step) : null
  if (d.unit && !one) return 'give the amount of one dose, more than 0'
  const plan = Object.fromEntries(moments.map(m => [m, typed(d.plan[m])]))
  if (Object.values(plan).some(v => v === undefined)) return 'an amount is a number of 0 or more'
  return { name: d.name, dose_unit: d.unit, step: one ?? null, comment: d.comment.trim(),
           plan: Object.fromEntries(moments.map(m => [m, plan[m] ?? 0])) }
}
// whether a draft differs from the saved supplement
const changed = (d: Draft, s: RegimeItem, moments: string[]) => {
  const fields = fieldsOf(d, moments)
  if (typeof fields === 'string') return true
  return fields.name.trim() !== s.name || fields.dose_unit !== s.dose_unit || fields.step !== s.step
    || fields.comment !== s.comment || moments.some(m => fields.plan[m] !== (s.plan[m] ?? 0))
}

// the regime of a day, the day of the page at first: any day can be chosen, an earlier one to see the plan it had, a
// later one to plan a change. Per supplement its unit, one dose, comment and the amount at each moment. The fields are drafts until
// Save, which saves every changed supplement from the chosen day on; Close or another day leaves the drafts unsaved. Remove
// and Add act at once, from the chosen day on. Copy gives another day the plan of the chosen day, from that day on. Below:
// every change of the plan, each a link to its day
function RegimeDialog({ day: pageDay, today, onClose }: { day: string; today: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  const [day, setDay] = useState(pageDay)
  const found = useQuery({ queryKey: ['health-regime', day], queryFn: () => getRegime(day) })
  return createPortal(
    <dialog ref={dialog} className="health-regime regime-dialog accent-health" onClose={onClose} aria-labelledby="regime-title" {...closeOutside}>
      <div className="analysis-head">
        <h2 id="regime-title">Regime from{' '}
          <input type="date" className="regime-day" value={day} aria-label="Day of the regime"
                 onChange={e => e.target.value && setDay(e.target.value)} />
        </h2>
        {day !== today && <button type="button" className="quiet" onClick={() => setDay(today)}>Today</button>}
        <button type="button" className="quiet" onClick={() => dialog.current?.close()}>Close</button>
      </div>
      <p className="muted health-hint">
        {day > today ? 'This day has not come yet: Save plans the change from this day on. ' : ''}
        Change the fields, then press Save. A change counts from this day on; the days before keep their plan. The amounts
        are in the unit of the supplement.
      </p>
      {found.isError && <p className="cap-error">{found.error.message}</p>}
      {found.data && <RegimeEditor key={day} day={day} regime={found.data.regime} onDay={setDay} />}
    </dialog>,
    document.body,
  )
}

function RegimeEditor({ day, regime, onDay }: { day: string; regime: Regime; onDay: (day: string) => void }) {
  const client = useQueryClient()
  const { moments, items, changes } = regime
  const [drafts, setDrafts] = useState<Record<number, Draft>>(() => Object.fromEntries(items.map(s => [s.id, draftOf(s, moments)])))
  // the regime after a change, for this dialog, the card and the history
  const show = (next: Regime) => {
    client.setQueryData(['health-regime', day], { regime: next })
    client.invalidateQueries({ queryKey: ['health'] })
    client.invalidateQueries({ queryKey: ['health-history'] })
  }
  // a supplement added or removed keeps the drafts of the others
  const keep = (next: Regime) => setDrafts(previous => Object.fromEntries(
    next.items.map(s => [s.id, previous[s.id] ?? draftOf(s, next.moments)])))
  const [draft, setDraft] = useState(EMPTY)
  const edited = items.filter(s => drafts[s.id] && changed(drafts[s.id], s, moments))
  const wrong = edited.flatMap(s => {
    const fields = fieldsOf(drafts[s.id], moments)
    return typeof fields === 'string' ? [`${drafts[s.id].name.trim() || s.name}: ${fields}`] : []
  })
  const save = useMutation({
    mutationFn: async () => {
      let next: Regime | null = null
      for (const s of edited) next = (await changeSupplement(s.id, day, fieldsOf(drafts[s.id], moments) as SupplementChangeFields)).regime
      return next
    },
    onSuccess: next => {
      if (!next) return
      show(next)
      setDrafts(Object.fromEntries(next.items.map(s => [s.id, draftOf(s, next.moments)])))
    },
    scope: SCOPE,
  })
  const remove = useMutation({ mutationFn: (id: number) => removeSupplement(id, day),
                               onSuccess: ({ regime: next }) => { show(next); keep(next) }, scope: SCOPE })
  const add = useMutation({
    mutationFn: () => addSupplement(day, { name: draft.name, dose_unit: draft.unit, step: draft.unit ? typed(draft.step) ?? null : null,
                                           comment: draft.comment.trim() }),
    onSuccess: ({ regime: next }) => { show(next); keep(next); setDraft(EMPTY) }, scope: SCOPE,
  })
  // the regime of this day copied to another day, from which it counts; the popup then shows that day
  const [target, setTarget] = useState('')
  const copy = useMutation({
    mutationFn: () => copyRegime(day, target),
    onSuccess: ({ regime: next }) => {
      client.invalidateQueries({ queryKey: ['health-regime'] })
      client.setQueryData(['health-regime', target], { regime: next })
      client.invalidateQueries({ queryKey: ['health'] })
      client.invalidateQueries({ queryKey: ['health-history'] })
      onDay(target)
    },
    scope: SCOPE,
  })
  const error = save.error ?? remove.error ?? add.error ?? copy.error
  return (
    <>
      <div className="regime-save regime-copy">
        <label>Copy this regime to{' '}
          <input type="date" className="regime-day" value={target} aria-label="Day to copy this regime to"
                 onChange={e => setTarget(e.target.value)} />
        </label>
        <button type="button" disabled={!target || target === day || edited.length > 0 || copy.isPending}
                onClick={() => copy.mutate()}>Copy</button>
        <span className="muted">
          {edited.length ? 'Save the changes first.' : `From that day on, the plan is the plan of ${shortDay(day)}.`}
        </span>
      </div>
      <div className="health-table regime-table">
        <table>
          <thead>
            <tr className="regime-groups">
              <th colSpan={4} scope="colgroup">Supplement</th>
              <th colSpan={moments.length + 1} scope="colgroup" className="regime-plan regime-plan-start">Plan, the amount at
                each moment</th>
              <td />
            </tr>
            <tr>
              <th scope="col">Name</th><th scope="col">Unit</th><th scope="col">One dose</th><th scope="col">Comment</th>
              {moments.map((m, i) => <th key={m} scope="col" className={plan(i)}>{m}</th>)}
              <th scope="col" className="regime-plan">A day</th><th scope="col"><span className="sr-only">Remove</span></th>
            </tr>
          </thead>
          {items.filter(s => drafts[s.id]).map(s => (
            <RegimeRow key={s.id} item={s} draft={drafts[s.id]} moments={moments} edited={edited.includes(s)}
                       onDraft={next => setDrafts({ ...drafts, [s.id]: next })} onRemove={() => remove.mutate(s.id)} />
          ))}
          <tbody>
            <tr className="regime-add">
              <td><input value={draft.name} placeholder="New supplement" aria-label="Name of the new supplement"
                         onChange={e => setDraft({ ...draft, name: e.target.value })} /></td>
              <td><UnitSelect value={draft.unit} label="Unit of the new supplement" onChange={unit => setDraft({ ...draft, unit })} /></td>
              <td><input type="number" min={0} step="any" value={draft.step} disabled={!draft.unit} placeholder={draft.unit ? 'e.g. 25' : ''}
                         aria-label="One dose of the new supplement" onChange={e => setDraft({ ...draft, step: e.target.value })} /></td>
              <td><input value={draft.comment} placeholder="e.g. with a meal" maxLength={200} aria-label="Comment on the new supplement"
                         onChange={e => setDraft({ ...draft, comment: e.target.value })} /></td>
              <td colSpan={moments.length + 2}>
                <button type="button" onClick={() => add.mutate()}
                        disabled={!draft.name.trim() || (!!draft.unit && !typed(draft.step)) || add.isPending}>Add</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div className="regime-save">
        <button type="button" className="primary" disabled={!edited.length || wrong.length > 0 || save.isPending}
                onClick={() => save.mutate()}>{save.isPending ? 'Saving…' : 'Save'}</button>
        <span className="muted">
          {wrong.length ? wrong.join('; ')
            : edited.length ? `${edited.length} ${edited.length === 1 ? 'supplement' : 'supplements'} changed, not saved yet`
              : 'No changes'}
        </span>
      </div>
      {error && <p className="cap-error">{error.message}</p>}
      {changes.length > 0 && (
        <section className="regime-changes">
          <h3>Changes</h3>
          <ul>
            {changes.map(c => (
              <li key={`${c.supplement_id}-${c.day}`}>
                <button type="button" className="link-button regime-change-day" aria-pressed={c.day === day}
                        title="Show the regime of this day" onClick={() => onDay(c.day)}>{shortDay(c.day)}</button>
                {' '}<strong>{c.name}</strong> {changeText(c)}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

function UnitSelect({ value, label, disabled = false, onChange }: {
  value: string; label: string; disabled?: boolean; onChange: (unit: string) => void
}) {
  return (
    <select value={value} aria-label={label} disabled={disabled} onChange={e => onChange(e.target.value)}
            title={disabled ? 'It has entries in this unit. Add a new supplement for another unit.' : undefined}>
      {UNITS.map(u => <option key={u} value={u}>{u}</option>)}
      <option value="">yes or no</option>
    </select>
  )
}

// one supplement of the regime: its fields change the draft only; Save sends it
function RegimeRow({ item: s, draft: d, moments, edited, onDraft, onRemove }: {
  item: RegimeItem; draft: Draft; moments: string[]; edited: boolean; onDraft: (next: Draft) => void; onRemove: () => void
}) {
  const amounts = Object.fromEntries(moments.map(m => [m, typed(d.plan[m]) ?? 0]))
  const total = Object.values(amounts).reduce((sum, v) => sum + v, 0)
  const setAmount = (m: string, value: string) => onDraft({ ...d, plan: { ...d.plan, [m]: value } })
  return (
    <tbody className={`regime-item${edited ? ' edited' : ''}`}>
      <tr>
        <td><input value={d.name} aria-label={`Name of ${s.name}`} onChange={e => onDraft({ ...d, name: e.target.value })} /></td>
        <td><UnitSelect value={d.unit} label={`Unit of ${s.name}`} disabled={s.used} onChange={unit => onDraft({ ...d, unit })} /></td>
        <td><input type="number" min={0} step="any" value={d.unit ? d.step : ''} disabled={!d.unit} aria-label={`One dose of ${s.name}`}
                   onChange={e => onDraft({ ...d, step: e.target.value })} /></td>
        <td><input value={d.comment} placeholder="e.g. with fat" maxLength={200} aria-label={`Comment on ${s.name}`}
                   onChange={e => onDraft({ ...d, comment: e.target.value })} /></td>
        {moments.map((m, i) => (
          <td key={m} className={plan(i)}>
            {d.unit
              ? <input type="number" min={0} step="any" value={d.plan[m]} placeholder="0" aria-label={`${s.name} at ${m}`}
                       onChange={e => setAmount(m, e.target.value)} />
              : <input type="checkbox" checked={amounts[m] === 1} aria-label={`${s.name} at ${m}`}
                       onChange={e => setAmount(m, e.target.checked ? '1' : '')} />}
          </td>
        ))}
        <td className="num regime-plan">{d.unit ? `${amount(total)} ${d.unit}` : total ? 'planned' : ''}</td>
        <td><button type="button" className="quiet danger" aria-label={`Remove ${s.name} from the list`} onClick={onRemove}>Remove</button></td>
      </tr>
    </tbody>
  )
}
