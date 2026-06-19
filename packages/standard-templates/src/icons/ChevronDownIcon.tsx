import { BaseIcon, type IconProps } from "./BaseIcon";

export function ChevronDownIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Angular chevron */}
      <path d="m6 9 6 6 6-6" />
      {/* Inner accent */}
      <path d="M9 11l3 3 3-3" strokeOpacity="0.3" strokeWidth="1" />
    </BaseIcon>
  );
}
