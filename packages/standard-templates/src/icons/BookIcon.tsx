import { BaseIcon, type IconProps } from "./BaseIcon";

export function BookIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <rect x="4" y="2" width="16" height="20" rx="2" stroke="currentColor" strokeWidth="inherit" />
      <line x1="8" y1="8"  x2="16" y2="8"  strokeLinecap="square" strokeOpacity="0.6" />
      <line x1="8" y1="12" x2="16" y2="12" strokeLinecap="square" strokeOpacity="0.6" />
      <line x1="8" y1="16" x2="13" y2="16" strokeLinecap="square" strokeOpacity="0.6" />
      <line x1="2" y1="5"  x2="2"  y2="19" strokeWidth="2.5" strokeLinecap="round" />
    </BaseIcon>
  );
}
