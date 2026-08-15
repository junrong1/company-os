/**
 * What an option costs, stated on the option rather than discovered in the report (R35).
 *
 * Its own file rather than an export from either surface that renders it. The conversation and
 * the tray both show the same three options at the same checkpoint, and the whole value of the
 * decision surface is that the two agree — a copy in each would be two prices for one option,
 * and the disagreement would only surface as a CEO deciding from the tray on figures the
 * conversation showed differently. It also keeps `Conversation.tsx` and `Panels.tsx` from
 * importing each other's values, which they currently avoid by importing only types.
 *
 * **Every figure carries the marking, and the marking is on the figure.** R28 is that no number
 * is presented without its marking travelling with it, so one badge above a list of four
 * numbers will not do: that is a claim about the list. The cost of repeating it is a slightly
 * busier row, which is the correct trade for the one claim this product cannot afford to get
 * wrong.
 */

import { type MetricDef, PAL, directionColour } from '../design/tokens'
import { Mark } from './Marking'
import {
  type DecisionOption,
  consequenceDirection,
  optionConsequence,
} from './conversation-model'

export interface OptionConsequenceProps {
  option: DecisionOption
  metricDefs: readonly MetricDef[]
}

/**
 * The figures one option moves, marked.
 *
 * An option whose effect map is empty renders no figures — the approval on the closing-cycle
 * item is the authored case. A row of zeros would read as a measurement that came back flat,
 * and there was no measurement; the note still renders, because "Approval threshold unchanged"
 * is exactly what that option is *for*.
 */
export function OptionConsequence({ option, metricDefs }: OptionConsequenceProps) {
  const figures = optionConsequence(option, metricDefs)
  const note = option.note ?? ''

  if (figures.length === 0 && note === '') return null

  return (
    <span className="consequence" data-figures={figures.length}>
      {figures.map((figure) => {
        const dir = consequenceDirection(figure, metricDefs)
        return (
          <span
            key={figure.key}
            className="consequence__figure"
            data-figure={figure.key}
            data-direction={dir}
          >
            <span className="consequence__label">{figure.label}</span>
            <span className="consequence__delta" style={{ color: directionColour(dir) }}>
              {figure.text}
            </span>
            <Mark of={`${figure.label} ${figure.text}`} />
          </span>
        )
      })}
      {note !== '' && (
        <span className="consequence__note" style={{ color: PAL.textFaint }}>
          Recorded as: {note}
        </span>
      )}
    </span>
  )
}
