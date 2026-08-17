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
 *
 * `Measured` is the one exception the completeness claim admits, and it lives here rather than
 * beside the tile that uses it so that the exception is as visible as the rule. Model calls and
 * tokens are counted rather than invented, so they carry the opposite label — under
 * `data-measured`, a *different* attribute, so the sweep can require exactly one of the two on
 * every figure instead of accepting either.
 */

import {
  AUTHORED_TUNING,
  DESCRIPTION,
  MEASURED,
  PAL,
  SCRIPTED,
  authoredTuningLabel,
  describedLabel,
  measuredLabel,
  scriptedLabel,
} from '../design/tokens'

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

/**
 * The marking for a figure that was counted rather than authored.
 *
 * Same construction, same chrome, same absence of hue — the only differences are the glyph,
 * the label and the attribute. Sharing the `.mark` class is deliberate: the two markings have
 * to sit at the same weight in the same place, or the measured one would read as a stronger
 * claim than a statement about provenance.
 */
export function Measured({ of, withLabel = false }: MarkProps) {
  return (
    <span
      className="mark"
      data-measured={MEASURED.glyph}
      style={{ color: PAL.textFaint }}
      title={MEASURED.description}
      aria-label={measuredLabel(of)}
    >
      <span className="mark__glyph" aria-hidden="true">
        {MEASURED.glyph}
      </span>
      {withLabel && <span className="mark__label">{MEASURED.label}</span>}
    </span>
  )
}

/**
 * The marking for a capability that is described rather than wired up (M16).
 *
 * Same construction and the same absence of hue as the two above, and a *third* attribute
 * rather than a reuse of either: `data-described` is a claim about a capability, and the two
 * figure markings are claims about a number. Sharing an attribute would let the completeness
 * sweep over figures be satisfied by a tool list, which is how a claim like R27's rots.
 */
export function Described({ of, withLabel = false }: MarkProps) {
  return (
    <span
      className="mark"
      data-described={DESCRIPTION.glyph}
      style={{ color: PAL.textFaint }}
      title={DESCRIPTION.description}
      aria-label={describedLabel(of)}
    >
      <span className="mark__glyph" aria-hidden="true">
        {DESCRIPTION.glyph}
      </span>
      {withLabel && <span className="mark__label">{DESCRIPTION.label}</span>}
    </span>
  )
}

/**
 * The marking for a scripted reply standing in for a briefing (M21).
 *
 * Same construction, same chrome, same absence of hue, and a *fourth* attribute. It is the strongest
 * claim of the four and the one whose absence would mislead most: the other three qualify something
 * the reader can see is a figure or a list, while this one says that a paragraph which reads like a
 * director's view is not one.
 *
 * `withLabel` defaults to *on*, unlike the other three. Those repeat per figure inside a tile, where
 * a spelled-out label every time would be noise; this appears once, above prose the reader is about
 * to take at face value, and the glyph alone would not stop them.
 */
export function Scripted({ of, withLabel = true }: MarkProps) {
  return (
    <span
      className="mark"
      data-scripted={SCRIPTED.glyph}
      style={{ color: PAL.textFaint }}
      title={SCRIPTED.description}
      aria-label={scriptedLabel(of)}
    >
      <span className="mark__glyph" aria-hidden="true">
        {SCRIPTED.glyph}
      </span>
      {withLabel && <span className="mark__label">{SCRIPTED.label}</span>}
    </span>
  )
}
