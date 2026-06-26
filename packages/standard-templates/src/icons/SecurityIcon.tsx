import { BaseIcon, type IconProps } from "./BaseIcon";

export function SecurityIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <path d="M12 2L4 6v6c0 5.5 3.5 10.5 8 12 4.5-1.5 8-6.5 8-12V6L12 2z" />
    </BaseIcon>
  );
}
