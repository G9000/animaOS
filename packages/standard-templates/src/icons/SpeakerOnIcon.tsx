import { BaseIcon, type IconProps } from "./BaseIcon";

export function SpeakerOnIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <path d="M3 9H7L13 4V20L7 15H3V9Z" />
      <path d="M16 8.5C17.8 9.8 17.8 14.2 16 15.5" strokeLinecap="round" />
      <path d="M19.5 5.5C22.8 7.8 22.8 16.2 19.5 18.5" strokeLinecap="round" />
    </BaseIcon>
  );
}
