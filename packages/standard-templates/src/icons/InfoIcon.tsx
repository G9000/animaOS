import { BaseIcon, type IconProps } from "./BaseIcon";

export function InfoIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <rect x="3" y="3" width="18" height="18" />
      <path d="M12 11v5" />
      <path d="M12 8v.5" strokeWidth="2" />
    </BaseIcon>
  );
}
