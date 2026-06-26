import { BaseIcon, type IconProps } from "./BaseIcon";

export function LinkIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <circle cx="6" cy="12" r="3" />
      <circle cx="18" cy="12" r="3" />
      <line x1="9" y1="12" x2="15" y2="12" />
    </BaseIcon>
  );
}
