import { glass as glassSurface } from "@anima/standard-templates";

/** The HUD bar: the shared glass surface plus the bar's own layout. */
export const glass = `relative flex items-center z-20 h-hud ${glassSurface}`;

export const CLIP_PATH = {
  cutBottomRight: "polygon(0 0, 100% 0, 100% calc(100% - 12px), calc(100% - 12px) 100%, 0 100%)",
  cutBottomLeft:  "polygon(0 0, 100% 0, 100% 100%, 12px 100%, 0 calc(100% - 12px))",
  cutTopLeft:     "polygon(12px 0, 100% 0, 100% 100%, 0 100%, 0 12px)",
  cutTopRight:    "polygon(0 0, calc(100% - 12px) 0, 100% 12px, 100% 100%, 0 100%)",
} as const;
