import { BaseIcon, type IconProps } from "./BaseIcon";

export function PlusIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      {/* Clean plus with subtle break */}
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </BaseIcon>
  );
}
