import { useState } from 'react'

// the clock time now, and the field of a time of the Health page (a unit of a supplement, a Ketostix reading)
export const clock = () => new Date().toTimeString().slice(0, 5)

// the time is typed hour and minutes apart: a save per change sent half-typed times, and the field jumped back to the
// stored time while a save ran. It keeps its own draft and saves when the user leaves it or presses Enter.
export function TimeField({ value, label, onSave }: { value: string; label: string; onSave: (at: string) => void }) {
  const [draft, setDraft] = useState(value)
  return (
    <input type="time" className="health-time" value={draft} aria-label={label} onChange={e => setDraft(e.target.value)}
           onBlur={() => (draft && draft !== value ? onSave(draft) : setDraft(value))}
           onKeyDown={e => {
             if (e.key === 'Enter') e.currentTarget.blur()
             if (e.key === 'Escape') { setDraft(value); e.currentTarget.blur() }
           }} />
  )
}
