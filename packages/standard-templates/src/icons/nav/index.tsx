import { BaseIcon, type IconProps } from "../BaseIcon";

// HOME — angular house with cut top-left corner
export function HomeIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={1.5}>
      <polygon points="12,3 22,11 19,11 19,21 14,21 14,15 10,15 10,21 5,21 5,11 2,11" />
      <line x1="2" y1="11" x2="4" y2="9" />
    </BaseIcon>
  );
}

// TASKS — checklist with angular tick
export function TasksIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={1.5}>
      <polyline points="4,6 6,8 9,4" />
      <line x1="13" y1="6" x2="21" y2="6" />
      <polyline points="4,12 6,14 9,10" />
      <line x1="13" y1="12" x2="21" y2="12" />
      <polyline points="4,18 6,20 9,16" />
      <line x1="13" y1="18" x2="21" y2="18" />
    </BaseIcon>
  );
}

// CHAT — angular speech bubble with cut corner
export function ChatIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={1.5}>
      <polygon points="3,3 21,3 21,16 9,16 3,21 3,3" />
      <line x1="7" y1="8" x2="17" y2="8" />
      <line x1="7" y1="12" x2="14" y2="12" />
    </BaseIcon>
  );
}

// MEM — layered data shards / fragmented stack
export function MemoryIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={1.5}>
      <polygon points="12,2 20,7 20,10 12,15 4,10 4,7" />
      <polyline points="4,12 12,17 20,12" />
      <polyline points="4,15 12,20 20,15" />
    </BaseIcon>
  );
}

// MIND — fractured circle / consciousness rings
export function MindIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={1.5}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12,2 L12,6" />
      <path d="M12,18 L12,22" />
      <path d="M2,12 L6,12" />
      <path d="M18,12 L22,12" />
      <path d="M5,5 L7.5,7.5" />
      <path d="M16.5,16.5 L19,19" />
      <path d="M19,5 L16.5,7.5" />
      <path d="M7.5,16.5 L5,19" />
    </BaseIcon>
  );
}

// MODS — hex grid / module cell
export function ModsIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={1.5}>
      <polygon points="12,2 20,7 20,17 12,22 4,17 4,7" />
      <polygon points="12,7 16,9.5 16,14.5 12,17 8,14.5 8,9.5" />
      <line x1="12" y1="2" x2="12" y2="7" />
      <line x1="12" y1="17" x2="12" y2="22" />
    </BaseIcon>
  );
}

// CFG — angular gear / settings cog with cut teeth
export function ConfigIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={1.5}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12,2 L12,5 M12,19 L12,22 M2,12 L5,12 M19,12 L22,12 M5.6,5.6 L7.8,7.8 M16.2,16.2 L18.4,18.4 M18.4,5.6 L16.2,7.8 M7.8,16.2 L5.6,18.4" />
      <rect x="10" y="2" width="4" height="3" />
      <rect x="10" y="19" width="4" height="3" />
      <rect x="2" y="10" width="3" height="4" />
      <rect x="19" y="10" width="3" height="4" />
    </BaseIcon>
  );
}

// DB — stacked cylinders / data tower
export function DatabaseIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={1.5}>
      <ellipse cx="12" cy="6" rx="8" ry="3" />
      <path d="M4,6 L4,12" />
      <path d="M20,6 L20,12" />
      <ellipse cx="12" cy="12" rx="8" ry="3" />
      <path d="M4,12 L4,18" />
      <path d="M20,12 L20,18" />
      <ellipse cx="12" cy="18" rx="8" ry="3" />
    </BaseIcon>
  );
}
