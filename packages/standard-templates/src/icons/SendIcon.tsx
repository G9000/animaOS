import { BaseIcon, type IconProps } from "./BaseIcon";

export function SendIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={1.5}>
      <path d="M5 12h14M14 7l5 5-5 5" />
    </BaseIcon>
  );
}
