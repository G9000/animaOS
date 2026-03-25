import { BaseIcon, type IconProps } from "./BaseIcon";

export function ChevronRightIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Angular bracket */}
      <path d="m9 6 6 6-6 6" />
      {/* Inner accent */}
      <path d="M11 9l3 3-3 3" strokeOpacity="0.3" strokeWidth="1" />
    </BaseIcon>
  );
}
