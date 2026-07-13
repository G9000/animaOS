import { BaseIcon, type IconProps } from "./BaseIcon";

export function ResetIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <polyline points="1 4 1 10 7 10" />
      <path d="M3.51 15a9 9 0 1 0 .49-4.95" />
    </BaseIcon>
  );
}
