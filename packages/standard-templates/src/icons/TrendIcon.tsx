import { BaseIcon, type IconProps } from "./BaseIcon";

export function TrendIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <polyline points="2,18 7,10 12,14 17,6 22,8" />
      <line x1="2" y1="22" x2="22" y2="22" />
    </BaseIcon>
  );
}
