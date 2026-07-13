export const glass =
  "relative flex items-center z-20 h-16 " +
  "bg-background/20 backdrop-blur-[44px] " +
  "border border-foreground/[0.08] " +
  "shadow-[0_8px_32px_rgba(0,0,0,0.20)]";

export const CLIP_PATH = {
  cutBottomRight: "polygon(0 0, 100% 0, 100% calc(100% - 12px), calc(100% - 12px) 100%, 0 100%)",
  cutBottomLeft:  "polygon(0 0, 100% 0, 100% 100%, 12px 100%, 0 calc(100% - 12px))",
  cutTopLeft:     "polygon(12px 0, 100% 0, 100% 100%, 0 100%, 0 12px)",
  cutTopRight:    "polygon(0 0, calc(100% - 12px) 0, 100% 12px, 100% 100%, 0 100%)",
} as const;
