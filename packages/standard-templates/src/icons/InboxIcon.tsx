import { BaseIcon, type IconProps } from "./BaseIcon";

export function InboxIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <rect x="2" y="14" width="20" height="8" />
      <line x1="12" y1="2" x2="12" y2="14" />
      <path d="M8 10L12 14L16 10" />
    </BaseIcon>
  );
}
