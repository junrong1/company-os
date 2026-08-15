/**
 * The authored-tuning marking, as one component every surface renders.
 *
 * R27 and R28 are a completeness claim — *every* figure the client renders says it is authored
 * tuning, and the marking travels with the figure rather than sitting once at the top of a
 * panel. A claim like that survives only if there is one thing to render: three hand-written
 * copies of a glyph and a label across the HUD, the conversation and the tray is three chances
 * for one of them to be dropped, and the drop would be invisible in review.
 *
 * So this file exists for the same reason `tokens.ts` does. The token says what the marking
 * *is* (R36: a glyph and a label, never a hue); this says what it *looks like on a surface*,
 * once.
 *
 * `data-authored-tuning` is the attribute the completeness sweep reads. It is on the element
 * rather than inferred from the glyph's text, so a tile can be checked without the test knowing
 * which character was chosen.
 */

import { AUTHORED_TUNING, PAL, authoredTuningLabel } from '../design/tokens'

export interface MarkProps {
  /** What is being marked, for the accessible label. */
  of: string
  /** Spell the label out beside the glyph. Off where a tile repeats it per figure. */
  withLabel?: boolean
}

/**
 * The marking itself.
 *
 * Colour is chrome, never signal: the glyph renders in the faint text value the rest of the
 * chrome uses, and every bit of the meaning is in the character and the label. Strip the colour
 * and the marking still reads, which is the property R36 asks for and the suite asserts.
 */
export function Mark({ of, withLabel = false }: MarkProps) {
  return (
    <span
      className="mark"
      data-authored-tuning={AUTHORED_TUNING.glyph}
      style={{ color: PAL.textFaint }}
      title={AUTHORED_TUNING.description}
      aria-label={authoredTuningLabel(of)}
    >
      <span className="mark__glyph" aria-hidden="true">
        {AUTHORED_TUNING.glyph}
      </span>
      {withLabel && <span className="mark__label">{AUTHORED_TUNING.label}</span>}
    </span>
  )
}
