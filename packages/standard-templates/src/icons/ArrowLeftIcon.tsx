import { BaseIcon, type IconProps } from "./BaseIcon";

export function ArrowLeftIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Circuit arrow */}
      <path d="M19 12H7" />
      <path d="M10 8l-4 4 4 4" />
      {/* Trace lines */}
      <path d="M21 12h-2M5 8v8" strokeOpacity="0.4" strokeWidth="1" />
    </BaseIcon>
  );
}
