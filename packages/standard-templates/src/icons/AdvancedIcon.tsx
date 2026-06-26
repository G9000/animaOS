import { BaseIcon, type IconProps } from "./BaseIcon";

export function AdvancedIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <path d="M3 6h18M7 12h10M11 18h2" />
    </BaseIcon>
  );
}
