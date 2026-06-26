import { BaseIcon, type IconProps } from "./BaseIcon";

export function LanguageIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 2c-3 3-4.5 6-4.5 10s1.5 7 4.5 10M12 2c3 3 4.5 6 4.5 10s-1.5 7-4.5 10M2 12h20" />
    </BaseIcon>
  );
}
