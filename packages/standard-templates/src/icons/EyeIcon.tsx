import { BaseIcon, type IconProps } from "./BaseIcon";

export function EyeIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Scanner/target style eye */}
      <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z" />
      <circle cx="12" cy="12" r="2.5" />
      {/* Crosshair accents */}
      <path d="M12 8v1.5M12 14.5V16M8 12h1.5M14.5 12H16" strokeOpacity="0.5" strokeWidth="1" />
    </BaseIcon>
  );
}
