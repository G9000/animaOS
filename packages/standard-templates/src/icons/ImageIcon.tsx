import { BaseIcon, type IconProps } from "./BaseIcon";

export function ImageIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Hologram/frame style */}
      <path d="M3 6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3v12a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V6z" />
      <circle cx="8.5" cy="8.5" r="1.5" />
      <path d="m21 15-5-5L5 21" strokeOpacity="0.7" />
      {/* Corner markers */}
      <path d="M6 2v2M18 2v2M6 20v2M18 20v2M2 6h2M2 18h2M20 6h2M20 18h2" strokeOpacity="0.4" strokeWidth="1" />
    </BaseIcon>
  );
}
