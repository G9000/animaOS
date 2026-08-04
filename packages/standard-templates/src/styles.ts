/**
 * Shared surface class names.
 *
 * These are thin handles onto the utility classes declared in `tokens.css`.
 * Import them instead of re-declaring a local `const glass = "..."`: that
 * pattern had produced thirteen copies across the desktop app, drifting to
 * five different blur radii and four different shadow ramps.
 */

/** The floating translucent panel that sits over the atmosphere layer. */
export const glass = "hw-glass";

/** The dashboard node card: softer shadow, slight radius. */
export const glassPanel = "hw-glass-panel";

/** `glass` with a deeper drop shadow, for surfaces that float well above the
 *  page — the chat composer, modals, popovers. */
export const glassRaised = "hw-glass hw-glass-raised";

/**
 * The signature offset-and-shadow affordance. Responds to hover *and* keyboard
 * focus; set `data-lifted="true"` to hold it in the lifted state (e.g. for the
 * active nav item).
 *
 * The element casts its shadow away from the screen edge it is docked to, so
 * pick the variant that matches where the element lives:
 *
 *   lift          up-left     docked bottom / right
 *   liftDownRight down-right  docked top-left
 *   liftUpRight   up-right    docked bottom-left
 *   liftX         rightward   item in a vertical rail
 *   liftXAccent   rightward   as liftX, with an accent-coloured shadow
 */
export const lift = "hw-lift";
export const liftDownRight = "hw-lift-dr";
export const liftUpRight = "hw-lift-ur";
export const liftX = "hw-lift-x";
export const liftXAccent = "hw-lift-x-accent";
