/**
 * The slot sentinels, for the dev-only scripts.
 *
 * A copy of the table in `src/render/cast/slots.ts`, and `tests/cast.test.ts` asserts the two
 * agree. Duplicated rather than imported because these scripts are plain `.mjs` run directly
 * by Node — importing a TypeScript module would mean a build step in front of a tool whose
 * whole value is being runnable with one command.
 *
 * The direction of the guarantee matters: the TypeScript file is the source of truth, this is
 * the copy, and the test fails if the copy drifts.
 */
export const SENTINELS = {
  skin: '#ff0000',
  skinShade: '#cc0000',
  skinLine: '#990000',
  hair: '#00ff00',
  hairLight: '#00cc00',
  hairDark: '#009900',
  top: '#0000ff',
  topShade: '#0000cc',
  topLine: '#000099',
  legs: '#ffff00',
  legsShade: '#cccc00',
  shoes: '#ff00ff',
  shoesShade: '#cc00cc',
  outline: '#000000',
  eye: '#00ffff',
  mouth: '#00cccc',
  accent: '#ff8800',
}
