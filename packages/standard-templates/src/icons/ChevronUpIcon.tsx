import { BaseIcon, type IconProps } from "./BaseIcon";

export function ChevronUpIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Angular chevron */}
      <path d="m6 15 6-6 6 6" />
      {/* Inner accent */}
      <path d="M9 13l3-3 3 3" strokeOpacity="0.3" strokeWidth="1" />
    </BaseIcon>
  );
}
