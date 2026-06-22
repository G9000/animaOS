import { BaseIcon, type IconProps } from "../BaseIcon";

// HOME — bold angular house with a clear doorway cut-out
export function HomeIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={2}>
      <path d="M4 10 12 3 20 10v10h-5v-7H9v7H4z" />
    </BaseIcon>
  );
}

// TASKS — checklist boxes with angular ticks
export function TasksIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={2}>
      <rect x="4" y="6" width="5" height="5" />
      <polyline points="5.5,10 7.5,8 9.5,6.5" />
      <line x1="12" y1="8.5" x2="20" y2="8.5" />
      <rect x="4" y="14" width="5" height="5" />
      <polyline points="5.5,18 7.5,16 9.5,14.5" />
      <line x1="12" y1="16.5" x2="18" y2="16.5" />
    </BaseIcon>
  );
}

// CHAT — angular speech bubble with a sharp tail
export function ChatIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={2}>
      <path d="M4 4h16v11h-7l-4 4v-4H4z" />
    </BaseIcon>
  );
}

// MEMORY — archive box / layered storage
export function MemoryIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={2}>
      <rect x="4" y="5" width="16" height="14" />
      <line x1="4" y1="10" x2="20" y2="10" />
      <line x1="8" y1="14" x2="16" y2="14" />
    </BaseIcon>
  );
}

// PRESENCE — radiating signal arcs around a core dot
export function PresenceIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={2}>
      <circle cx="12" cy="12" r="2.5" />
      <path d="M5 12a7 7 0 0 1 14 0" />
      <path d="M8 12a4 4 0 0 1 8 0" />
    </BaseIcon>
  );
}

// MIND — central node with radiating connections
export function MindIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={2}>
      <circle cx="12" cy="12" r="2.5" fill="currentColor" />
      <line x1="12" y1="3" x2="12" y2="8" />
      <line x1="12" y1="16" x2="12" y2="21" />
      <line x1="3" y1="12" x2="8" y2="12" />
      <line x1="16" y1="12" x2="21" y2="12" />
      <line x1="5.5" y1="5.5" x2="9" y2="9" />
      <line x1="15" y1="15" x2="18.5" y2="18.5" />
      <line x1="18.5" y1="5.5" x2="15" y2="9" />
      <line x1="5.5" y1="18.5" x2="9" y2="15" />
    </BaseIcon>
  );
}

// MODS — nested hex cells
export function ModsIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={2}>
      <polygon points="12,2 20,7 20,17 12,22 4,17 4,7" />
      <polygon points="12,8 16,10.5 16,15.5 12,18 8,15.5 8,10.5" />
    </BaseIcon>
  );
}

// CFG — clean gear with 8 teeth
export function ConfigIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={2}>
      <circle cx="12" cy="12" r="3" />
      <line x1="12" y1="2" x2="12" y2="5" />
      <line x1="12" y1="19" x2="12" y2="22" />
      <line x1="2" y1="12" x2="5" y2="12" />
      <line x1="19" y1="12" x2="22" y2="12" />
      <line x1="4.5" y1="4.5" x2="7" y2="7" />
      <line x1="17" y1="17" x2="19.5" y2="19.5" />
      <line x1="19.5" y1="4.5" x2="17" y2="7" />
      <line x1="4.5" y1="19.5" x2="7" y2="17" />
    </BaseIcon>
  );
}

// DB — stacked cylinders / data tower
export function DatabaseIcon(props: IconProps) {
  return (
    <BaseIcon {...props} strokeWidth={2}>
      <ellipse cx="12" cy="6" rx="8" ry="3" />
      <path d="M4 6v6" />
      <path d="M20 6v6" />
      <ellipse cx="12" cy="12" rx="8" ry="3" />
      <path d="M4 12v6" />
      <path d="M20 12v6" />
      <ellipse cx="12" cy="18" rx="8" ry="3" />
    </BaseIcon>
  );
}
