import { BaseIcon, type IconProps } from "./BaseIcon";

export function SpeakerOffIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <path d="M3 9H7L13 4V20L7 15H3V9Z" />
      <path d="M16 9L21 15" />
      <path d="M21 9L16 15" />
    </BaseIcon>
  );
}
