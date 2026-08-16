#!/usr/bin/env node
/**
 * Cast the eleven identities: three appearances each, from their approved candidates.
 *
 * The six colours come out of the candidate art by `palette-from-candidate.mjs`. The three
 * choices — hair, outfit, accessory — are made here, and they are made *deterministically
 * from the candidate id* rather than at random, for two reasons. A random casting would
 * differ on every run of this script, so a re-run would silently redress the whole company.
 * And spreading the choices through the id's own hash is what makes A, B and C of one person
 * differ in more than one feature, which is the success criterion the redesign set.
 *
 *   node scripts/make-manifests.mjs
 */

import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { basename, join } from 'node:path'

import { extract } from './palette-from-candidate.mjs'
import { readPng } from './png.mjs'

const CANDIDATES = new URL(
  '../../docs/assets/company-os-visual-redesign/roster-characters/',
  import.meta.url,
)

/** The prototype's `hash(s)`, including its 32-bit truncation. The client uses the same one. */
function idHash(text) {
  let accumulator = 0
  for (let index = 0; index < text.length; index += 1) {
    accumulator = (accumulator * 31 + text.charCodeAt(index)) | 0
  }
  return Math.abs(accumulator)
}

const HAIR_COUNT = 8
const OUTFIT_COUNT = 8
const ACCESSORY_COUNT = 6

/**
 * Which features a candidate wears.
 *
 * Each feature is hashed from the id with its own salt rather than strided out of one hash.
 * `dir_sales-a` and `dir_sales-b` hash one apart, and striding one hash leaves consecutive
 * ids agreeing on everything but the fastest-varying slot — so A and B of a person would have
 * come out in the same outfit with the same accessory and only their hair different.
 *
 * One slot more than there are accessories, so somebody carries nothing: a cast in which
 * everybody wears a lanyard has no lanyard signal, only decoration.
 */
function features(id) {
  const accessory = idHash(`${id}:accessory`) % (ACCESSORY_COUNT + 1)
  return {
    hair: idHash(`${id}:hairShape`) % HAIR_COUNT,
    outfit: idHash(`${id}:outfit`) % OUTFIT_COUNT,
    accessory: accessory === ACCESSORY_COUNT ? null : accessory,
  }
}

const files = readdirSync(CANDIDATES)
  .filter((name) => name.endsWith('.png'))
  .sort()

/**
 * The one casting decision that is not cosmetic.
 *
 * R12 makes the player the strongest cool accent in the office, which means the CEO's top is
 * a requirement rather than a look. Extraction reads `you-c` as 鸽蓝 — a near-black, because
 * that candidate's jacket is very dark — and a CEO in near-black is not an accent at all.
 *
 * So the three CEO appearances are pinned to 石绿, the value their own candidates carry and
 * the one `VISUAL_DESIGN.md` §2 names for the player. Everything else about them still comes
 * out of their art; this is the correction by eye the extraction was always going to need,
 * and it is the only one, because it is the only slot with a rule attached.
 */
const CEO_TOP = '#2f8c8b'
const CEO_ACCENT = '#57c3c2'

const entries = files.map((file) => {
  const id = basename(file, '.png')
  const skin = extract(readPng(readFileSync(join(CANDIDATES.pathname, file))), id)
  if (id.startsWith('you-')) {
    skin.top = CEO_TOP
    skin.accent = CEO_ACCENT
  }
  return { id, ...features(id), skin }
})

/**
 * A/B/C of one identity must differ in at least two features.
 *
 * The redesign's success criterion, checked here rather than only in the suite so a re-run
 * that produces a bad casting says so at the moment it happens. When a collision does occur
 * the fix is to walk that candidate's accessory one step, which is the least visible of the
 * four and the one a person is least likely to have been cast for.
 */
const byIdentity = new Map()
for (const entry of entries) {
  const identity = entry.id.slice(0, entry.id.lastIndexOf('-'))
  if (!byIdentity.has(identity)) byIdentity.set(identity, [])
  byIdentity.get(identity).push(entry)
}

function differences(a, b) {
  let count = 0
  if (a.hair !== b.hair) count += 1
  if (a.outfit !== b.outfit) count += 1
  if (a.accessory !== b.accessory) count += 1
  if (a.skin.skin !== b.skin.skin) count += 1
  if (a.skin.top !== b.skin.top) count += 1
  return count
}

for (const [identity, group] of byIdentity) {
  for (let i = 0; i < group.length; i += 1) {
    for (let j = i + 1; j < group.length; j += 1) {
      let guard = 0
      while (differences(group[i], group[j]) < 2 && guard < ACCESSORY_COUNT + 1) {
        const next = group[j].accessory === null ? 0 : group[j].accessory + 1
        group[j].accessory = next > ACCESSORY_COUNT - 1 ? null : next
        guard += 1
      }
      if (differences(group[i], group[j]) < 2) {
        throw new Error(`${identity}: ${group[i].id} and ${group[j].id} cannot be told apart`)
      }
    }
  }
}

const body = [
  '/**',
  ' * GENERATED — do not edit by hand.',
  ' *',
  ' * Built by `scripts/make-manifests.mjs` from the approved casting board in',
  ' * `docs/assets/company-os-visual-redesign/roster-characters/`. The six colours are read out',
  ' * of each candidate rather than transcribed, because a hand-typed hex digit is a colour',
  ' * nobody notices is wrong; the three feature choices are derived from the candidate id, so a',
  ' * re-run casts the same company rather than redressing it.',
  ' */',
  '',
  "import type { Manifest } from './compose'",
  '',
  '/** Three appearances for each of the eleven identities the office can show. */',
  'export const CAST: Record<string, Manifest[]> = {',
  ...[...byIdentity.entries()].map(([identity, group]) =>
    [
      `  ${identity}: [`,
      ...group.map((entry) =>
        [
          '    {',
          `      hair: ${entry.hair},`,
          `      outfit: ${entry.outfit},`,
          `      accessory: ${entry.accessory === null ? 'null' : entry.accessory},`,
          '      skin: {',
          ...['skin', 'hair', 'top', 'legs', 'shoes', 'accent'].map(
            (slot) => `        ${slot}: '${entry.skin[slot]}',`,
          ),
          '      },',
          '    },',
        ].join('\n'),
      ),
      '  ],',
    ].join('\n'),
  ),
  '}',
  '',
].join('\n')

writeFileSync(new URL('../src/render/cast/manifests.ts', import.meta.url), body)
console.log(`src/render/cast/manifests.ts — ${byIdentity.size} identities, ${entries.length} appearances`)
