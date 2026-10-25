import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { closeOutside } from './dialog'

// how to measure each site of the tape measure the same way every week: a front view of a male body with the numbered
// measure lines, in the order of the form, and the rules next to it. The figure faces the reader: the person's left arm
// and leg are on the reader's right
const SITES: [string, string][] = [
  ['Chest', 'At nipple level, with the arms down, at the end of a normal breath out.'],
  ['Waist (navel)', 'At the navel, standing, with the belly relaxed, at the end of a normal breath out.'],
  ['Hips', 'Around the widest part of the buttocks, with the feet together.'],
  ['Upper arm, left', 'The arm hanging relaxed: halfway between the shoulder tip (acromion) and the elbow point (olecranon).'],
  ['Upper arm, right', 'The same distance below the shoulder tip as on the left.'],
  ['Thigh, left', 'Standing with the weight on both legs: a fixed distance above the top of the kneecap, for example 15 cm.'],
  ['Thigh, right', 'The same distance above the kneecap as on the left.'],
]
const RULES = [
  'Upper arms and thighs: put the tape at the same height every week. With a second tape or a ruler, measure down from '
    + 'the shoulder tip (for example 17 cm) and up from the top of the kneecap (for example 15 cm). Write these two '
    + 'distances down and use them every week.',
  'The same day of the week, in the morning, before breakfast and before exercise: after a workout the arms and thighs can '
    + 'be 1 to 2 cm larger.',
  'Keep the tape level all around (check it in a mirror), close on the skin without pressing in. A tape with a spring '
    + 'handle gives the same tension every time.',
  'Measure 3 times and keep the middle value.',
  'Read the trend, not one value: a change below about 1 cm in a week is probably noise.',
]

// a numbered badge at the end of a measure line
function Badge({ n, x, y }: { n: number; x: number; y: number }) {
  return (
    <g className="guide-badge">
      <circle cx={x} cy={y} r={8} />
      <text x={x} y={y + 3.5} textAnchor="middle">{n}</text>
    </g>
  )
}

// one arm hanging from its shoulder joint, turned a little away from the body; side 1 is the person's left (the reader's
// right). The band lies halfway between the shoulder tip and the elbow; its number stays upright beside the arm
const ARM_TURN = 14
function Arm({ side, n }: { side: 1 | -1; n: number }) {
  const joint = { x: 100 + side * 36, y: 80 }
  const turn = (-side * ARM_TURN * Math.PI) / 180
  const at = (lx: number, ly: number) => ({ x: joint.x + lx * Math.cos(turn) - ly * Math.sin(turn),
                                            y: joint.y + lx * Math.sin(turn) + ly * Math.cos(turn) })
  const badge = at(side * 21, 36)
  return (
    <>
      <g transform={`translate(${joint.x} ${joint.y}) rotate(${-side * ARM_TURN})`}>
        <rect className="guide-body" x={-9.5} y={-6} width={19} height={80} rx={9.5} />
        <rect className="guide-body" x={-7.5} y={66} width={15} height={74} rx={7.5} />
        <ellipse className="guide-body" cx={0} cy={148} rx={7} ry={11} />
        <circle className="guide-point" cx={0} cy={71} r={2.2} />
        <line className="guide-line" x1={-12} x2={12} y1={36} y2={36} />
      </g>
      <Badge n={n} x={badge.x} y={badge.y} />
    </>
  )
}

// one leg from the hip to the foot; side 1 is the person's left. The thigh band is a fixed distance above the kneecap
function Leg({ side, n }: { side: 1 | -1; n: number }) {
  const x = (v: number) => 100 + side * v
  return (
    <g>
      <path className="guide-body"
            d={`M ${x(1)} 200 L ${x(31)} 192 Q ${x(35)} 250 ${x(23)} 306 L ${x(5)} 306 Q ${x(2)} 250 ${x(1)} 200 Z`} />
      <path className="guide-body"
            d={`M ${x(5)} 300 L ${x(23)} 300 Q ${x(27)} 345 ${x(17)} 398 L ${x(7)} 398 Q ${x(3)} 345 ${x(5)} 300 Z`} />
      <ellipse className="guide-body" cx={x(13)} cy={402} rx={10} ry={5} />
      <circle className="guide-point" cx={x(14)} cy={294} r={2.2} />
      <line className="guide-line" x1={x(-1)} x2={x(36)} y1={262} y2={262} />
      <Badge n={n} x={x(46)} y={262} />
    </g>
  )
}

function Figure() {
  return (
    <svg className="guide-figure" viewBox="0 0 200 430" role="img"
         aria-label="Front view of a male body with the seven measure lines: chest, waist, hips, both upper arms and both thighs">
      <Leg side={-1} n={7} />
      <Leg side={1} n={6} />
      <ellipse className="guide-body" cx={100} cy={30} rx={16} ry={20} />
      <rect className="guide-body" x={92} y={44} width={16} height={22} rx={5} />
      <path className="guide-body"
            d={'M 66 78 Q 69 66 84 64 L 116 64 Q 131 66 134 78 Q 132 92 128 100 Q 131 128 126 152 Q 134 172 132 200 '
               + 'L 68 200 Q 66 172 74 152 Q 69 128 72 100 Q 68 92 66 78 Z'} />
      <Arm side={-1} n={5} />
      <Arm side={1} n={4} />
      <circle className="guide-point" cx={66} cy={74} r={2.2} />
      <circle className="guide-point" cx={134} cy={74} r={2.2} />
      <circle className="guide-point" cx={100} cy={152} r={2.2} />
      <line className="guide-line" x1={71} x2={129} y1={106} y2={106} />
      <line className="guide-line" x1={74} x2={126} y1={152} y2={152} />
      <line className="guide-line" x1={67} x2={133} y1={190} y2={190} />
      <Badge n={1} x={86} y={106} />
      <Badge n={2} x={86} y={152} />
      <Badge n={3} x={86} y={190} />
      <text className="guide-side" x={70} y={424} textAnchor="middle">right</text>
      <text className="guide-side" x={130} y={424} textAnchor="middle">left</text>
    </svg>
  )
}

function GuideDialog({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  return createPortal(
    <dialog ref={dialog} className="health-guide accent-health" onClose={onClose} aria-labelledby="guide-title" {...closeOutside}>
      <div className="analysis-head">
        <h2 id="guide-title">How to measure</h2>
        <button type="button" className="quiet" onClick={() => dialog.current?.close()}>Close</button>
      </div>
      <div className="guide-layout">
        <Figure />
        <div className="guide-text">
          <p className="muted">Circumference in cm, the tape level all around. The figure faces you: the person's left side
            is on your right.</p>
          <ol className="guide-sites">
            {SITES.map(([name, how]) => <li key={name}><strong>{name}</strong> {how}</li>)}
          </ol>
          <h3>Every time</h3>
          <ul className="guide-rules">
            {RULES.map(rule => <li key={rule}>{rule}</li>)}
          </ul>
        </div>
      </div>
    </dialog>,
    document.body,
  )
}

export function TapeGuideButton() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" className="quiet" onClick={() => setOpen(true)}>How to measure</button>
      {open && <GuideDialog onClose={() => setOpen(false)} />}
    </>
  )
}
