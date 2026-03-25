import { BaseIcon, type IconProps } from "./BaseIcon";

export function ArrowRightIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Circuit arrow */}
      <path d="M5 12h12" />
      <path d="M14 8l4 4-4 4" />
      {/* Trace lines */}
      <path d="M3 12h2M19 8v8" strokeOpacity="0.4" strokeWidth="1" />
    </BaseIcon>
  );
}
