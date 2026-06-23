import { BaseIcon, type IconProps } from "./BaseIcon";

export function VaultIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <rect x="2" y="3" width="20" height="14" rx="1" />
      <circle cx="12" cy="10" r="3" />
      <path d="M12 13v2M19 17v2M5 17v2" />
    </BaseIcon>
  );
}
