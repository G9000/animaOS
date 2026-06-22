import { BaseIcon, type IconProps } from "./BaseIcon";

export function DocumentIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Page with folded corner */}
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8L14 2z" />
      <path d="M14 2v6h6" />
      {/* Text lines */}
      <line x1="8" y1="13" x2="16" y2="13" />
      <line x1="8" y1="17" x2="13" y2="17" />
    </BaseIcon>
  );
}
