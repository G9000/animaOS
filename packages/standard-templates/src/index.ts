export { cn } from "./utils/cn";

// ── Primitives ─────────────────────────────────────────────────────────────
export { Alert, type AlertProps, type AlertVariant } from "./primitives/Alert";
export { Badge, type BadgeProps } from "./primitives/Badge";
export {
  Button,
  type ButtonProps,
  type ButtonVariant,
  type ButtonSize,
} from "./primitives/Button";
export { buttonVariants } from "./primitives/Button";
export { DotLoader, type DotLoaderProps } from "./primitives/DotLoader";
export { Input, type InputProps } from "./primitives/Input";
export { Label, type LabelProps } from "./primitives/Label";
export { LoadingText, type LoadingTextProps } from "./primitives/LoadingText";
export { Textarea, type TextareaProps } from "./primitives/Textarea";
export { Toggle, type ToggleProps } from "./primitives/Toggle";

// ── Composed ───────────────────────────────────────────────────────────────
export { AttachMenu, type AttachMenuProps } from "./composed/AttachMenu";
export { Field, type FieldProps } from "./composed/Field";
export { PageHeader, type PageHeaderProps } from "./composed/PageHeader";
export { PromptInput, type PromptInputProps } from "./composed/PromptInput";
export { TabBar, type TabBarProps, type Tab } from "./composed/TabBar";
export {
  ToastContainer,
  showToast,
  showSuccess,
  showError,
  showWarning,
  showInfo,
  type ToastData,
  type ToastType,
  type ToastContainerProps,
} from "./composed/Toast";

// ── Icons ───────────────────────────────────────────────────────────────────
export { type IconProps } from "./icons/BaseIcon";
export {
  PlusIcon,
  ImageIcon,
  FileIcon,
  DocumentIcon,
  MicIcon,
  SendIcon,
  EyeIcon,
  EyeOffIcon,
  ArrowRightIcon,
  ArrowLeftIcon,
  ChevronRightIcon,
  XIcon,
} from "./icons";
export {
  HomeIcon,
  TasksIcon,
  ChatIcon,
  MemoryIcon,
  MindIcon,
  ModsIcon,
  ConfigIcon,
  DatabaseIcon,
} from "./icons/nav";

// ── ASCII Art ───────────────────────────────────────────────────────────────
export {
  useAnimaSymbol,
  useAnimaLogo,
  useAnimaSymbolSpinning,
  useGlowLine,
  useAsciiText,
  useAsciiDots,
  type GlowChar,
  LOGO_SVG_PATH,
  DENSITY,
  SPARKLE_CHARS,
  ANIMA_ART,
  BG_DUST,
  GLOW,
  hash,
} from "./ascii-art";
