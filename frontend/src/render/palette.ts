/**
 * Avatar palettes, derived from an id.
 *
 * No photographs and no avatar assets: a person's colour is a function of their id, and
 * the initials are always shown alongside it. Nobody's likeness is used.
 *
 * The hash is a direct port of the prototype's `hash(s)`, and the `| 0` in it is
 * load-bearing rather than incidental. It coerces the accumulator to a *signed* 32-bit
 * integer on every iteration, so the value wraps and can go negative before `Math.abs`
 * folds it back. An implementation using unbounded integers agrees for short ids and
 * diverges for long ones — which is why the golden vector includes an id long enough to
 * wrap the accumulator several times.
 *
 * `h * 31` stays exact as a double for any signed 32-bit `h` (the product fits well
 * inside 2^53), so the arithmetic below is bit-identical to the prototype's without
 * needing `Math.imul`.
 */

export const AV_LIGHT = [
  '#2a55c9',
  '#0f7b6c',
  '#a8541c',
  '#6b46c1',
  '#1d6fb8',
  '#8a5a00',
  '#b3261e',
  '#3f6212',
] as const

export const AV_DARK = [
  '#5b82ea',
  '#2f9c8a',
  '#c9762f',
  '#8f6ee0',
  '#3f8fd6',
  '#b58325',
  '#d9564b',
  '#5f8f2a',
] as const

/** The prototype's `hash(s)`, 32-bit truncation included. */
export function idHash(text: string): number {
  let accumulator = 0
  for (let index = 0; index < text.length; index += 1) {
    accumulator = (accumulator * 31 + text.charCodeAt(index)) | 0
  }
  return Math.abs(accumulator)
}

/** Which palette slot an id lands in. */
export function paletteIndex(id: string): number {
  return idHash(id) % AV_LIGHT.length
}

/** The avatar colour for an id, in the given theme. */
export function avatarColor(id: string, dark: boolean): string {
  const palette = dark ? AV_DARK : AV_LIGHT
  return palette[paletteIndex(id)]
}
