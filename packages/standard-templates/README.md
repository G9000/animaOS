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
├── components/
│   ├── Button.tsx          # Fill-animation button (CVA) — variant, size, icon, loading
│   ├── Input.tsx           # Text input
│   ├── Field.tsx           # Labeled field wrapper
│   ├── Label.tsx           # Form label
│   ├── Alert.tsx           # Inline message — error / warning / info
│   ├── Badge.tsx           # Tiny chip label
│   ├── Toggle.tsx          # Boolean pill toggle
│   ├── TabBar.tsx          # Horizontal tab navigation
│   ├── PageHeader.tsx      # Page-level header with title, meta, actions
│   ├── DotLoader.tsx       # Three-dot pulse loader
│   ├── LoadingText.tsx     # Pulsing monospace loading label
│   └── Toast.tsx           # Imperative toast system (ToastContainer + showToast helpers)
│
└── ascii-art/
    ├── useAnimaSymbol.ts        # Core Anima symbol — shimmer, glow, optional center text
    ├── useAnimaSymbolSpinning.ts # Spinning variant
    ├── useAnimaLogo.ts          # ANIMA block-letter logo
    ├── useGlowLine.ts           # Animated horizontal separator
    ├── useAsciiText.ts          # Character-resolve text animation
    ├── useAsciiDots.ts          # Drifting dot-field background
    └── constants.ts             # Shared art assets and hash fn
```

## Theming

Tokens follow the `.dark` class on `<html>`. Toggle with:

```ts
document.documentElement.classList.toggle("dark");
```
