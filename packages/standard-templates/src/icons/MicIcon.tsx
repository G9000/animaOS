import { BaseIcon, type IconProps } from "./BaseIcon";

export function MicIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Audio input / waveform sensor */}
      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" x2="12" y1="19" y2="22" />
      {/* Signal bars */}
      <path d="M8 17v1M16 17v1" strokeOpacity="0.5" strokeWidth="1" />
    </BaseIcon>
  );
}
