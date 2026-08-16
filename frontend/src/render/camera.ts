/**
 * The viewport, and why the office needed one.
 *
 * The renderer used to size its canvas to the whole floor and let `.stage` scroll. That was
 * survivable at 496×288 and is not at 992×576: on a 1280×800 laptop the stage gets roughly
 * 450 pixels of height after the chrome, which does not fit eighteen rows at any integer
 * zoom, so the CEO walks off the visible area and the player loses them. AE4 asks that no
 * character become obscured at any supported width, and this is what makes that true.
 *
 * Three rules, and each of them is a thing that goes wrong without it:
 *
 * **A dead zone.** The camera does not move while the player is near the middle. Following
 * every pixel makes the room swim under a walking character, which reads as the floor being
 * unstable rather than as the camera being attentive.
 *
 * **Whole pixels.** The translation is rounded before the integer zoom multiplies it. A
 * fractional offset resamples every pixel in the office and it stops being pixel art — the
 * same reason `chooseZoom` refuses a fractional zoom.
 *
 * **Clamped to the building.** The viewport never shows a coordinate outside the floor, so
 * the office never appears to float on a background that does not exist.
 */

/** A viewport position, in logical pixels before the zoom. */
export interface Viewport {
  x: number
  y: number
}

/**
 * How much of the viewport the player can move within before the camera follows.
 *
 * Expressed as a fraction of the viewport rather than as pixels, so the feel is the same on a
 * laptop and on a large display instead of the dead zone being most of one and a sliver of
 * the other.
 */
export const DEAD_ZONE = 0.34

/**
 * Where the viewport should sit to keep a target visible.
 *
 * Pure, and separate from the class below, so "the camera clamps to the building" and "the
 * camera does not move inside the dead zone" are properties a test can state about one call
 * rather than about a sequence of them.
 */
export function follow(
  current: Viewport,
  target: Viewport,
  view: { width: number; height: number },
  world: { width: number; height: number },
): Viewport {
  const axis = (
    from: number,
    to: number,
    viewSize: number,
    worldSize: number,
  ): number => {
    // The floor fits: centre it and never move. Following inside a viewport that already
    // shows everything would slide the room around for no reason.
    if (worldSize <= viewSize) return Math.round((worldSize - viewSize) / 2)

    const margin = (viewSize * DEAD_ZONE) / 2
    const low = from + viewSize / 2 - margin
    const high = from + viewSize / 2 + margin

    let next = from
    if (to < low) next = from - (low - to)
    else if (to > high) next = from + (to - high)

    // Clamped last, so a target near a wall stops the camera at the wall rather than being
    // followed past it.
    return Math.round(Math.min(Math.max(next, 0), worldSize - viewSize))
  }

  return {
    x: axis(current.x, target.x, view.width, world.width),
    y: axis(current.y, target.y, view.height, world.height),
  }
}

/**
 * The camera, as the renderer holds it.
 *
 * State is one `Viewport`, because a dead zone is inherently stateful — "has the player left
 * the middle" is a question about where the camera already is.
 */
export class Camera {
  private position: Viewport = { x: 0, y: 0 }

  get x(): number {
    return this.position.x
  }

  get y(): number {
    return this.position.y
  }

  /** Move toward the target, honouring the dead zone and the building's edges. */
  track(
    target: Viewport,
    view: { width: number; height: number },
    world: { width: number; height: number },
  ): Viewport {
    this.position = follow(this.position, target, view, world)
    return this.position
  }

  /**
   * Jump straight to centring on a target, with no dead zone.
   *
   * For the first frame of a run and for a resync: easing in from the origin would open every
   * session with the camera sliding across the office, which reads as a loading animation
   * nobody asked for.
   */
  centreOn(
    target: Viewport,
    view: { width: number; height: number },
    world: { width: number; height: number },
  ): Viewport {
    const axis = (to: number, viewSize: number, worldSize: number): number => {
      if (worldSize <= viewSize) return Math.round((worldSize - viewSize) / 2)
      return Math.round(Math.min(Math.max(to - viewSize / 2, 0), worldSize - viewSize))
    }

    this.position = {
      x: axis(target.x, view.width, world.width),
      y: axis(target.y, view.height, world.height),
    }
    return this.position
  }

  reset(): void {
    this.position = { x: 0, y: 0 }
  }
}
