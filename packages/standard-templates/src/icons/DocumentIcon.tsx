import { BaseIcon, type IconProps } from "./BaseIcon";

export function DocumentIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Data file with sharp corners */}
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8L14 2z" />
      <path d="M14 2v6h6" strokeOpacity="0.6" />
      {/* Data lines */}
      <path d="M8 12h8M8 16h5" strokeOpacity="0.5" strokeWidth="1" />
    </BaseIcon>
  );
}
