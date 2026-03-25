import { BaseIcon, type IconProps } from "./BaseIcon";

export function XIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Sharp X with slight offset for tech feel */}
      <path d="M5 5l14 14" />
      <path d="M19 5L5 19" />
      {/* Subtle inner accent */}
      <path d="M7 7l10 10M17 7L7 17" strokeOpacity="0.3" strokeWidth="1" />
    </BaseIcon>
  );
}
