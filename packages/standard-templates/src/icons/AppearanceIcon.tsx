import { BaseIcon, type IconProps } from "./BaseIcon";

export function AppearanceIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v4M12 18v4M2 12h4M18 12h4" />
    </BaseIcon>
  );
}
