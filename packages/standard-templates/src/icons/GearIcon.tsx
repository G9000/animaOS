import { BaseIcon, type IconProps } from "./BaseIcon";

export function GearIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 4.5V6M12 18V19.5M4.5 12H6M18 12H19.5M6.7 6.7L7.8 7.8M16.2 16.2L17.3 17.3M17.3 6.7L16.2 7.8M7.8 16.2L6.7 17.3" />
    </BaseIcon>
  );
}
