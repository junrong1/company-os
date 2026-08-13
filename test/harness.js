/* Runs company-os.html's logic against a DOM stub to confirm the full loop:
   assign -> work -> stop for a decision -> decide -> deliverable. */
const fs = require('fs');
const vm = require('vm');
/* Resolved relative to this file, so the folder can be moved anywhere.
   Note: do not bind a variable called `path` here — the harness pulls the
   app's own BFS `path()` out of the sandbox under that name. */
const TARGET = require('path').resolve(__dirname, '..', 'company-os.html');


const src = fs.readFileSync(TARGET, 'utf8');
const script = src.match(/<script>([\s\S]*)<\/script>/)[1];

/* ---- Minimal DOM stub ---- */
const listeners = {};
function makeEl(id) {
  const el = {
    id, dataset: {}, style: {}, textContent: '', innerHTML: '', hidden: false,
    className: '', children: [], value: '',
    setAttribute() {}, getAttribute: () => null, removeAttribute() {},
    addEventListener(t, f) { (listeners[id] = listeners[id] || {})[t] = f; },
    querySelector: () => makeEl('q'), querySelectorAll: () => [],
    closest: () => null, getContext: () => ctx2d,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 960, height: 576 }),
    appendChild() {}, focus() {},
    clientWidth: 1000, clientHeight: 600,
    get parentElement() { return makeEl('stage'); },
    toDataURL: () => 'data:,',
  };
  return el;
}
const ctx2d = new Proxy({}, {
  get: (t, k) => {
    if (k === 'measureText') return () => ({ width: 20 });
    if (k === 'createRadialGradient') return () => ({ addColorStop() {} });
    return () => {};
  },
  set: () => true,
});
const els = {};
const el = (id) => (els[id] = els[id] || makeEl(id));

const documentStub = {
  documentElement: { getAttribute: () => 'light', setAttribute() {} },
  getElementById: el,
  querySelector: () => makeEl('q'),
  querySelectorAll: () => [],
  createElement: () => makeEl('canvas'),
  addEventListener(t, f) { (listeners.doc = listeners.doc || {})[t] = f; },
};

let rafQueue = [];
const sandbox = {
  document: documentStub,
  window: {
    addEventListener() {},
    devicePixelRatio: 1,
    matchMedia: () => ({ matches: false, addEventListener() {} }),
  },
  performance: { now: () => t },
  getComputedStyle: () => ({ getPropertyValue: () => '#888888' }),
  requestAnimationFrame: (f) => rafQueue.push(f),
  setTimeout: () => 0,
  clearTimeout: () => {},
  MutationObserver: class { observe() {} },
  ResizeObserver: class { observe() {} },
  console,
  Math, JSON, Object, Array, String, Number, Map, Set, Proxy, Infinity, NaN, isNaN,
};
sandbox.globalThis = sandbox;
let t = 0;

const ctx = vm.createContext(sandbox);
vm.runInContext(script, ctx, { filename: 'company-os' });

const S = vm.runInContext('S', ctx);
const item = vm.runInContext('item', ctx);
const byId = vm.runInContext('byId', ctx);
const assignViaManager = vm.runInContext('assignViaManager', ctx);
const SPAWN = vm.runInContext('SPAWN', ctx);
const ROOMS = vm.runInContext('ROOMS', ctx);
const resolve = vm.runInContext('resolve', ctx);

function advance(seconds) {
  const stepMs = 16;
  for (let i = 0; i < (seconds * 1000) / stepMs; i++) {
    t += stepMs;
    const q = rafQueue; rafQueue = [];
    q.forEach((f) => f(t));
  }
}

const fail = [];
const check = (label, cond, extra) => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${extra ? '  — ' + extra : ''}`);
  if (!cond) fail.push(label);
};

/* 1. Initial state */
check('everyone starts idle', S.people.every((p) => p.state === 'idle'));
check('visibility starts at 6%', S.m.visibility === 6);
check('5 directives available at start', S.items.filter((w) => !w.requires).length === 5,
  `ids ${S.items.filter((w) => !w.requires).map((w) => w.id).join(',')}`);
check('3 directives locked at start', S.items.filter((w) => w.requires).length === 3);

/* 2. Assign through the reporting line */
const w = item('wi_ap_map');
const mgr = byId('dir_admin'), staff = byId('stf_ap');
assignViaManager(w, mgr);
check('director walks to hand off', mgr.state === 'walking', `path ${mgr.path.length} cells`);
advance(12);
check('specialist started the work', staff.state === 'working' && staff.itemId === 'wi_ap_map', `state=${staff.state}`);
check('director returns to their desk', mgr.state === 'idle' || mgr.state === 'walking', `state=${mgr.state}`);

/* 3. Work stalls at the checkpoint */
advance(40);
check('stops at the decision point', staff.state === 'blocked' && w.status === 'blocked',
  `state=${staff.state} progress=${Math.round((w.done / w.effort) * 100)}%`);
const beforeStall = w.done;
advance(10);
check('no progress until you decide', Math.abs(w.done - beforeStall) < 0.001,
  `${beforeStall.toFixed(2)} → ${w.done.toFixed(2)}`);
check('one item waiting in the tray', S.items.filter((x) => x.status === 'blocked').length === 1);

/* 4. In-person decisions surface tacit knowledge */
const visBefore = S.m.visibility, moraleBefore = S.m.morale;
resolve(w, 0, 0, true);
check('work resumes after the decision', staff.state === 'working' && w.status === 'active');
check('in-person decision raises visibility', S.m.visibility === visBefore + 6 + 2,
  `${visBefore} → ${S.m.visibility} (option +6 / in person +2)`);
check('in-person decision raises morale', S.m.morale === moraleBefore - 1 + 2, `${moraleBefore} → ${S.m.morale}`);
check('tacit knowledge recorded', !!w.decisions[0].tacit);

/* 5. Completion produces a deliverable with provenance */
advance(60);
check('work completed', w.status === 'done', `progress=${Math.round((w.done / w.effort) * 100)}%`);
check('one deliverable produced', S.outputs.length === 1, S.outputs[0] && S.outputs[0].title);
check('deliverable carries provenance', S.outputs.length > 0 && S.outputs[0].provenance.length >= 2,
  S.outputs[0] && S.outputs[0].provenance.join(' / '));
check('specialist back to idle', staff.state === 'idle' && staff.itemId === null);
check('follow-up work unlocked', vm.runInContext('unlocked', ctx)(item('wi_ap_auto')));

/* 6. Bypassing the director costs morale */
const w2 = item('wi_dup_entry');
const m2 = S.m.morale;
vm.runInContext('assignDirect', ctx)(w2, byId('stf_order'));
check('direct assignment costs 3 morale', S.m.morale === m2 - 3, `${m2} → ${S.m.morale}`);
check('specialist started moving', ['working', 'walking'].includes(byId('stf_order').state));

/* 7. Tray decisions get no bonus */
advance(70);
const w3 = item('wi_dup_entry');
if (w3.status === 'blocked') {
  const v3 = S.m.visibility;
  resolve(w3, 0, 1, false);
  check('tray decision gets no in-person bonus', S.m.visibility === v3, `${v3} → ${S.m.visibility}`);
  check('tray decision records no tacit knowledge', w3.decisions[0].tacit === null);
} else {
  check('second item also reaches a decision', false, `status=${w3.status} progress=${Math.round((w3.done / w3.effort) * 100)}%`);
}

/* 8. Time passes and fixed costs accrue */
check('days advanced', S.day > 1, `Day ${S.day} ${S.hour.toFixed(1)}h`);
check('cash reduced by fixed costs', S.m.cash < 4800, `${S.m.cash}K`);

/* 9. Pathfinding respects walls */
const path = vm.runInContext('path', ctx);
const walkable = vm.runInContext('walkable', ctx);
const far = S.people[4].seat;
const p1 = path(SPAWN, far);
check('path found to a distant desk', p1.length > 6, `${p1.length} cells to ${far.x},${far.y}`);
check('path never crosses a wall', p1.every((c) => walkable(c.x, c.y)));
check('every desk is reachable', S.people.every((p) => path(SPAWN, p.seat).length > 0),
  `grid ${vm.runInContext('COLS', ctx)}x${vm.runInContext('ROWS', ctx)}, ${ROOMS.length} rooms`);
check('every person got a distinct desk', new Set(S.people.map(p => p.seat.x + ',' + p.seat.y)).size === S.people.length,
  S.people.map(p => p.name.split(' ')[0] + '@' + p.seat.x + ',' + p.seat.y).join(' '));

console.log('\n' + (fail.length ? `FAILED: ${fail.length} -> ${fail.join(' / ')}` : 'ALL PASS'));
process.exit(fail.length ? 1 : 0);
