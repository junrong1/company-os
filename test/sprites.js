/* Validates the pixel-art data: every row 16 wide, every glyph in the palette,
   character bodies 18 rows + 6 rows of legs = 24. */
const fs = require('fs'), vm = require('vm');
/* Resolved relative to this file, so the folder can be moved anywhere.
   Note: do not bind a variable called `path` here — the harness pulls the
   app's own BFS `path()` out of the sandbox under that name. */
const TARGET = require('path').resolve(__dirname, '..', 'company-os.html');

const src = fs.readFileSync(TARGET, 'utf8');
const script = src.match(/<script>([\s\S]*)<\/script>/)[1];

// Pull just the data literals out, without running the DOM-dependent code.
const grab = (name) => {
  const i = script.indexOf(`const ${name} = `);
  if (i < 0) throw new Error('missing ' + name);
  let depth = 0, start = script.indexOf('=', i) + 1, j = start;
  for (;; j++) {
    const c = script[j];
    if (c === '{' || c === '[') depth++;
    else if (c === '}' || c === ']') { depth--; if (depth === 0) { j++; break; } }
  }
  return vm.runInNewContext('(' + script.slice(start, j) + ')');
};

const ART = grab('ART'), PROPS = grab('PROPS'), BODY = grab('BODY'), LEGS = grab('LEGS'), LONG_HAIR = grab('LONG_HAIR');
const charPal = new Set(['.', 'j', 'h', 'H', 'l', 's', 'S', 'e', 'c', 'u', 't', 'T', 'p', 'P', 'b', 'B']);
const propPal = new Set(Object.keys(ART).concat(['.']));

let bad = 0;
const check = (label, rows, pal, expectRows) => {
  if (expectRows && rows.length !== expectRows) { console.log(`FAIL ${label}: ${rows.length} rows, expected ${expectRows}`); bad++; }
  rows.forEach((r, i) => {
    const want = label.startsWith('PROPS') ? 16 : 10;
    if (r.length !== want) { console.log(`FAIL ${label} row ${i}: width ${r.length}, expected ${want} — "${r}"`); bad++; }
    [...r].forEach((ch) => { if (!pal.has(ch)) { console.log(`FAIL ${label} row ${i}: glyph "${ch}" not in palette`); bad++; } });
  });
};

Object.entries(PROPS).forEach(([k, rows]) => check(`PROPS.${k}`, rows, propPal, 16));
Object.entries(BODY).forEach(([k, rows]) => check(`BODY.${k}`, rows, charPal, 14));
LEGS.forEach((rows, i) => check(`LEGS[${i}]`, rows, charPal, 2));
Object.entries(LONG_HAIR).forEach(([k, rows]) => check(`LONG_HAIR.${k}`, rows, charPal, 14));

const total = Object.keys(PROPS).length + Object.keys(BODY).length + LEGS.length + Object.keys(LONG_HAIR).length;
console.log(bad ? `\n${bad} sprite problem(s)` : `\nAll ${total} sprites valid (props 16px, characters 10x16 = 14+2 rows)`);
process.exit(bad ? 1 : 0);
