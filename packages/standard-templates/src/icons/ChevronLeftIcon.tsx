import { BaseIcon, type IconProps } from "./BaseIcon";

export function ChevronLeftIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Angular bracket */}
      <path d="m15 6-6 6 6 6" />
      {/* Inner accent */}
      <path d="M13 9l-3 3 3 3" strokeOpacity="0.3" strokeWidth="1" />
    </BaseIcon>
  );
}
