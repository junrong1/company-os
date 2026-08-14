/**
 * Re-exports, so the suites import the store from one place.
 *
 * Nothing is wrapped or faked here. The point is only that `hud.test.ts` and `dag.test.ts`
 * agree about which store they are driving — two suites reaching for the module by different
 * paths is how you end up with two module instances and a test that passes because the other
 * one's state never arrived.
 */

export { type Frame, type EventFrame, type ItemStatus, useRunStore } from '../../src/net/store'
export type { MetricDef as MetricDefLike } from '../../src/design/tokens'
