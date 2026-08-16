/**
 * GENERATED — do not edit by hand.
 *
 * Built by `scripts/make-cast-atlas.mjs` from the approved casting board in
 * `docs/assets/company-os-visual-redesign/roster-characters/`. Every pixel of every frame
 * is a pixel that board approved; the script adds directions and a stride and draws
 * nothing.
 */

/** One candidate: which row of the atlas it occupies, and the six colours it is made of. */
export interface CastEntry {
  row: number
  skin: {
    skin: string
    hair: string
    top: string
    legs: string
    shoes: string
    accent: string
  }
}

export const CELL_WIDTH = 48
export const CELL_HEIGHT = 64
export const SHEET_FRAMES = 4
export const SHEET_FACINGS = 4

/** Every candidate on the board, keyed `<identity>-<a|b|c>`. */
export const ATLAS: Record<string, CastEntry> = {
  'dir_admin-a': { row: 0, skin: { skin: '#b56743', hair: '#3a2728', top: '#f1c8ae', legs: '#3f4b63', shoes: '#a86745', accent: '#c98662' } },
  'dir_admin-b': { row: 1, skin: { skin: '#b56743', hair: '#3a2728', top: '#70452f', legs: '#253147', shoes: '#3f4b63', accent: '#a86745' } },
  'dir_admin-c': { row: 2, skin: { skin: '#d89a52', hair: '#3a2728', top: '#f1c8ae', legs: '#3f4b63', shoes: '#c98662', accent: '#b56743' } },
  'dir_cs-a': { row: 3, skin: { skin: '#d89a52', hair: '#3a2728', top: '#b56743', legs: '#4b302b', shoes: '#65443a', accent: '#d6a04d' } },
  'dir_cs-b': { row: 4, skin: { skin: '#d89a52', hair: '#3a2728', top: '#b56743', legs: '#70452f', shoes: '#3f4b63', accent: '#d68b69' } },
  'dir_cs-c': { row: 5, skin: { skin: '#b56743', hair: '#3a2728', top: '#70452f', legs: '#3f4b63', shoes: '#4b302b', accent: '#d89a52' } },
  'dir_hr-a': { row: 6, skin: { skin: '#a86745', hair: '#3a2728', top: '#70452f', legs: '#253147', shoes: '#3f4b63', accent: '#d89a52' } },
  'dir_hr-b': { row: 7, skin: { skin: '#d89a52', hair: '#3a2728', top: '#b9a7d7', legs: '#3f4b63', shoes: '#b56743', accent: '#d68b69' } },
  'dir_hr-c': { row: 8, skin: { skin: '#d89a52', hair: '#a8adb4', top: '#f1c8ae', legs: '#3f4b63', shoes: '#74759b', accent: '#b56743' } },
  'dir_sales-a': { row: 9, skin: { skin: '#b56743', hair: '#3a2728', top: '#5a8fb7', legs: '#253147', shoes: '#70452f', accent: '#2376b7' } },
  'dir_sales-b': { row: 10, skin: { skin: '#d68b69', hair: '#9d7f5f', top: '#93b5cf', legs: '#4b302b', shoes: '#65443a', accent: '#2376b7' } },
  'dir_sales-c': { row: 11, skin: { skin: '#d89a52', hair: '#c98662', top: '#5a8fb7', legs: '#3a2728', shoes: '#8b5d47', accent: '#2376b7' } },
  'stf_ap-a': { row: 12, skin: { skin: '#d89a52', hair: '#3a2728', top: '#9d7f5f', legs: '#4b302b', shoes: '#a86745', accent: '#d6a04d' } },
  'stf_ap-b': { row: 13, skin: { skin: '#b56743', hair: '#3a2728', top: '#a86745', legs: '#253147', shoes: '#65443a', accent: '#d89a52' } },
  'stf_ap-c': { row: 14, skin: { skin: '#d89a52', hair: '#3a2728', top: '#9d7f5f', legs: '#3f4b63', shoes: '#65443a', accent: '#d6a04d' } },
  'stf_buyer-a': { row: 15, skin: { skin: '#d89a52', hair: '#3a2728', top: '#9d7f5f', legs: '#4b302b', shoes: '#70452f', accent: '#d6a04d' } },
  'stf_buyer-b': { row: 16, skin: { skin: '#d89a52', hair: '#aa6a4c', top: '#f1c8ae', legs: '#3a2728', shoes: '#70452f', accent: '#b56743' } },
  'stf_buyer-c': { row: 17, skin: { skin: '#70452f', hair: '#a86745', top: '#f1c8ae', legs: '#3a2728', shoes: '#65758b', accent: '#b56743' } },
  'stf_cs-a': { row: 18, skin: { skin: '#d89a52', hair: '#4b302b', top: '#d68b69', legs: '#3a2728', shoes: '#fff8e7', accent: '#b56743' } },
  'stf_cs-b': { row: 19, skin: { skin: '#70452f', hair: '#3a2728', top: '#c85c5c', legs: '#3f4b63', shoes: '#fff8e7', accent: '#d89a52' } },
  'stf_cs-c': { row: 20, skin: { skin: '#d89a52', hair: '#70452f', top: '#c85c5c', legs: '#3a2728', shoes: '#3f4b63', accent: '#b56743' } },
  'stf_field-a': { row: 21, skin: { skin: '#d89a52', hair: '#3a2728', top: '#5a8fb7', legs: '#4b302b', shoes: '#70452f', accent: '#2376b7' } },
  'stf_field-b': { row: 22, skin: { skin: '#b56743', hair: '#3a2728', top: '#93b5cf', legs: '#253147', shoes: '#fff8e7', accent: '#2376b7' } },
  'stf_field-c': { row: 23, skin: { skin: '#d89a52', hair: '#70452f', top: '#57c3c2', legs: '#3f4b63', shoes: '#4b302b', accent: '#2376b7' } },
  'stf_order-a': { row: 24, skin: { skin: '#d89a52', hair: '#3a2728', top: '#93b5cf', legs: '#253147', shoes: '#65758b', accent: '#2376b7' } },
  'stf_order-b': { row: 25, skin: { skin: '#b56743', hair: '#3a2728', top: '#93b5cf', legs: '#3f4b63', shoes: '#70452f', accent: '#2376b7' } },
  'stf_order-c': { row: 26, skin: { skin: '#b56743', hair: '#70452f', top: '#5a8fb7', legs: '#3f4b63', shoes: '#a8adb4', accent: '#2376b7' } },
  'stf_rec-a': { row: 27, skin: { skin: '#d89a52', hair: '#3a2728', top: '#b9a7d7', legs: '#3f4b63', shoes: '#fff8e7', accent: '#b56743' } },
  'stf_rec-b': { row: 28, skin: { skin: '#a86745', hair: '#3a2728', top: '#70452f', legs: '#253147', shoes: '#4b302b', accent: '#d89a52' } },
  'stf_rec-c': { row: 29, skin: { skin: '#b56743', hair: '#70452f', top: '#b9a7d7', legs: '#65758b', shoes: '#65443a', accent: '#d6a04d' } },
  'you-a': { row: 30, skin: { skin: '#b56743', hair: '#3a2728', top: '#2f8c8b', legs: '#253147', shoes: '#f1c8ae', accent: '#1ba784' } },
  'you-b': { row: 31, skin: { skin: '#d89a52', hair: '#70452f', top: '#2f8c8b', legs: '#3f4b63', shoes: '#253147', accent: '#b56743' } },
  'you-c': { row: 32, skin: { skin: '#a86745', hair: '#3a2728', top: '#2f8c8b', legs: '#253147', shoes: '#3f4b63', accent: '#d6a04d' } },
}
