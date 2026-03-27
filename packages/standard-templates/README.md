# @anima/standard-templates

Design system for AnimaOS. Tailwind CSS v4 + React.

## Setup

```ts
import "@anima/standard-templates/tokens.css"; // once at app entry
import { Button, cn } from "@anima/standard-templates";
```

## Structure

```
src/
├── tokens.css              # Design tokens (light + dark), animations
├── index.ts                # All exports
│
├── utils/
│   └── cn.ts               # clsx + tailwind-merge helper
│
├── primitives/             # Basic UI building blocks
│   ├── Alert.tsx           # Inline message — error / warning / info
│   ├── Badge.tsx           # Tiny chip label
│   ├── Button.tsx          # Fill-animation button (CVA) — variant, size, icon, loading
│   ├── DotLoader.tsx       # Three-dot pulse loader
│   ├── Input.tsx           # Text input
│   ├── Label.tsx           # Form label
│   ├── LoadingText.tsx     # Pulsing monospace loading label
│   └── Toggle.tsx          # Boolean pill toggle
│
├── composed/               # Compound components
│   ├── AttachMenu.tsx      # Attachment options menu
│   ├── Field.tsx           # Labeled field wrapper (Label + Input)
│   ├── PageHeader.tsx      # Page-level header with title, meta, actions
│   ├── PromptInput.tsx     # Chat prompt input with attachments
│   ├── TabBar.tsx          # Horizontal tab navigation
│   └── Toast.tsx           # Imperative toast system (ToastContainer + showToast helpers)
│
├── icons/                  # Icon components
│   ├── ArrowLeftIcon.tsx
│   ├── ArrowRightIcon.tsx
│   ├── BaseIcon.tsx
│   ├── ChevronRightIcon.tsx
│   ├── DocumentIcon.tsx
│   ├── EyeIcon.tsx
│   ├── EyeOffIcon.tsx
│   ├── FileIcon.tsx
│   ├── ImageIcon.tsx
│   ├── MicIcon.tsx
│   ├── PlusIcon.tsx
│   ├── SendIcon.tsx
│   └── XIcon.tsx
│
└── ascii-art/              # Terminal-style animation hooks
    ├── constants.ts             # Shared art assets and hash fn
    ├── useAnimaLogo.ts          # ANIMA block-letter logo
    ├── useAnimaSymbol.ts        # Core Anima symbol — shimmer, glow, optional center text
    ├── useAnimaSymbolSpinning.ts # Spinning variant
    ├── useAsciiDots.ts          # Drifting dot-field background
    ├── useAsciiText.ts          # Character-resolve text animation
    └── useGlowLine.ts           # Animated horizontal separator
```

## Theming

Tokens follow the `.dark` class on `<html>`. Toggle with:

```ts
document.documentElement.classList.toggle("dark");
```
