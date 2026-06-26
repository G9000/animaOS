import { BaseIcon, type IconProps } from "./BaseIcon";

export function SparkleIcon(props: IconProps) {
  return (
    <BaseIcon {...props}>
      <path d="M12 2L13.5 10.5L22 12L13.5 13.5L12 22L10.5 13.5L2 12L10.5 10.5L12 2Z" />
    </BaseIcon>
  );
}
