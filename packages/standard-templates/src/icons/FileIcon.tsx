import { BaseIcon, type IconProps } from "./BaseIcon";

export function FileIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Attachment/data link icon */}
      <path d="M8 6a6 6 0 0 1 10.83 3.5l-3.46 3.46a4 4 0 0 0-5.66-5.66L4.5 12.5A6 6 0 0 0 15.5 18l3-3" strokeOpacity="0.8" />
      <path d="M9 12.5a3 3 0 0 0 5.1 2.1l3.5-3.5a3 3 0 0 0-4.24-4.24L8.5 11.5" strokeWidth="1.5" />
    </BaseIcon>
  );
}
