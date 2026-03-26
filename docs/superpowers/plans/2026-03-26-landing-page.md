# AnimaOS Landing Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a static marketing landing page for AnimaOS with waitlist signup, blog, and docs/thesis sections.

**Architecture:** Astro 5 app at `apps/site/` in the monorepo. Tailwind CSS v4 for styling with the desktop app's color palette. React islands for interactive components (ASCII art, waitlist form). Astro Content Collections for markdown blog and docs.

**Tech Stack:** Astro 5, Tailwind CSS v4, React 19 (islands), Bun, Content Collections (markdown)

---

## File Structure

```
apps/site/
  package.json
  astro.config.mjs
  tsconfig.json
  src/
    styles/
      global.css                  -- Tailwind + theme tokens + fonts + animations
    layouts/
      BaseLayout.astro            -- HTML shell, head, fonts, nav, footer
      PostLayout.astro            -- Blog post layout (frontmatter title/date)
      DocLayout.astro             -- Docs/thesis layout
    components/
      Nav.astro                   -- Top navigation bar
      Footer.astro                -- Footer links
      Hero.astro                  -- Hero section with CTA
      Problem.astro               -- "Every AI remembers" section
      Vision.astro                -- The Core concept section
      Features.astro              -- 6 feature cards
      HowItWorks.astro            -- 3-step flow
      OpenSource.astro            -- Open source + AI-built section
      Waitlist.astro              -- Repeated waitlist CTA
      WaitlistForm.tsx            -- React island: email input + submit
      AnimaSymbol.tsx             -- React island: ASCII art animation
    content/
      blog/                       -- Blog posts (markdown)
        hello-world.md            -- Placeholder first post
      docs/                       -- Thesis docs (markdown, copied from docs/thesis/)
        whitepaper.md
        inner-life.md
        portable-core.md
        succession-protocol.md
    content.config.ts             -- Content collection schemas
    pages/
      index.astro                 -- Landing page (composes all sections)
      blog/
        index.astro               -- Blog index
        [...slug].astro           -- Blog post page
      docs/
        index.astro               -- Docs index
        [...slug].astro           -- Doc page
  public/
    favicon.svg                   -- Simple favicon
```

---

### Task 1: Scaffold Astro Project

**Files:**
- Create: `apps/site/package.json`
- Create: `apps/site/astro.config.mjs`
- Create: `apps/site/tsconfig.json`
- Create: `apps/site/src/styles/global.css`

- [ ] **Step 1: Create `apps/site/package.json`**

```json
{
  "name": "site",
  "type": "module",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "astro dev",
    "build": "astro build",
    "preview": "astro preview"
  },
  "dependencies": {
    "astro": "^5.9.0",
    "@astrojs/react": "^4.4.0",
    "@astrojs/tailwind": "^6.0.0",
    "@tailwindcss/vite": "^4.1.18",
    "tailwindcss": "^4.1.18",
    "@tailwindcss/typography": "^0.5.19",
    "react": "^19.1.0",
    "react-dom": "^19.1.0",
    "@fontsource/space-grotesk": "^5.2.10",
    "@fontsource/space-mono": "^5.2.9",
    "@fontsource/playfair-display": "^5.2.8",
    "@anima/standard-templates": "workspace:*"
  },
  "devDependencies": {
    "@types/react": "^19.1.8",
    "@types/react-dom": "^19.1.6",
    "typescript": "~5.8.3"
  }
}
```

- [ ] **Step 2: Create `apps/site/astro.config.mjs`**

```js
import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  integrations: [react()],
  vite: {
    plugins: [tailwindcss()],
  },
});
```

- [ ] **Step 3: Create `apps/site/tsconfig.json`**

```json
{
  "extends": "astro/tsconfigs/strict",
  "compilerOptions": {
    "jsx": "react-jsx"
  }
}
```

- [ ] **Step 4: Create `apps/site/src/styles/global.css`**

Port the theme tokens from `apps/desktop/src/index.css` — same color palette, fonts, animations, but standalone (no `@anima/standard-templates/tokens.css` import since we only need the marketing subset):

```css
@import "tailwindcss";

@import "@fontsource/space-grotesk/400.css";
@import "@fontsource/space-grotesk/500.css";
@import "@fontsource/space-grotesk/700.css";
@import "@fontsource/space-mono/400.css";
@import "@fontsource/space-mono/700.css";
@import "@fontsource/playfair-display/400.css";
@import "@fontsource/playfair-display/400-italic.css";

@plugin "@tailwindcss/typography";

@theme {
  --color-bg: #08090e;
  --color-bg-card: #0c0d14;
  --color-bg-input: #10111a;
  --color-bg-surface: #13141e;
  --color-border: #252740;
  --color-border-active: #383a58;
  --color-text: #c8c8d4;
  --color-text-muted: #8898a8;
  --color-primary: #5ea0ab;
  --color-primary-hover: #4a8892;
  --color-danger: #d04848;
  --color-success: #48a060;
  --color-warning: #c89848;
  --color-accent: #5ea0ab;
  --font-mono: "Space Mono", "JetBrains Mono", "Fira Code", monospace;
  --font-serif: "Playfair Display", Georgia, serif;
  --font-sans:
    "Space Grotesk", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI",
    sans-serif;
}

body {
  @apply bg-bg text-text font-sans antialiased min-h-screen leading-relaxed;
}

::selection {
  background: rgba(94, 160, 171, 0.18);
}

::-webkit-scrollbar {
  width: 4px;
  height: 4px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: #252740;
}

/* Animations */
@keyframes fade-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
.animate-fade-in {
  animation: fade-in 0.3s ease-out forwards;
}

@keyframes slide-up {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}
.animate-slide-up {
  animation: slide-up 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

@keyframes text-glow {
  0%, 100% { text-shadow: 0 0 2px currentColor; }
  50% { text-shadow: 0 0 8px currentColor; }
}
.animate-text-glow {
  animation: text-glow 2s ease-in-out infinite;
}

@keyframes cursor-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
.animate-cursor {
  animation: cursor-blink 1s step-end infinite;
}
```

- [ ] **Step 5: Install dependencies**

Run: `cd apps/site && bun install`

- [ ] **Step 6: Add dev script to root package.json**

Add to root `package.json` scripts:
```json
"dev:site": "cd apps/site && bun run dev"
```

- [ ] **Step 7: Verify Astro starts**

Run: `cd apps/site && bun run dev`
Expected: Astro dev server starts on port 4321

- [ ] **Step 8: Commit**

```bash
git add apps/site/package.json apps/site/astro.config.mjs apps/site/tsconfig.json apps/site/src/styles/global.css package.json
git commit -m "feat(site): scaffold Astro landing page project"
```

---

### Task 2: Base Layout + Navigation + Footer

**Files:**
- Create: `apps/site/src/layouts/BaseLayout.astro`
- Create: `apps/site/src/components/Nav.astro`
- Create: `apps/site/src/components/Footer.astro`
- Create: `apps/site/src/pages/index.astro` (placeholder)
- Create: `apps/site/public/favicon.svg`

- [ ] **Step 1: Create `apps/site/public/favicon.svg`**

Minimal teal diamond favicon:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" fill="#08090e"/>
  <polygon points="16,4 28,16 16,28 4,16" fill="#5ea0ab"/>
</svg>
```

- [ ] **Step 2: Create `apps/site/src/components/Nav.astro`**

```astro
---
const links = [
  { href: "/#features", label: "Features" },
  { href: "/blog", label: "Blog" },
  { href: "/docs", label: "Docs" },
];
---

<nav class="fixed top-0 left-0 right-0 z-50 border-b border-border bg-bg/80 backdrop-blur-md">
  <div class="mx-auto max-w-5xl flex items-center justify-between px-6 py-4">
    <a href="/" class="font-mono text-sm tracking-[0.3em] uppercase text-primary hover:text-text transition-colors">
      ANIMA
    </a>
    <div class="flex items-center gap-8">
      {links.map((link) => (
        <a
          href={link.href}
          class="font-mono text-xs tracking-[0.2em] uppercase text-text-muted hover:text-text transition-colors"
        >
          {link.label}
        </a>
      ))}
      <a
        href="https://github.com/leocairos/animaOS"
        target="_blank"
        rel="noopener noreferrer"
        class="font-mono text-xs tracking-[0.2em] uppercase text-text-muted hover:text-text transition-colors"
      >
        GitHub
      </a>
    </div>
  </div>
</nav>
```

Note: The GitHub URL is a placeholder — update to the real repo URL.

- [ ] **Step 3: Create `apps/site/src/components/Footer.astro`**

```astro
---
const year = new Date().getFullYear();
---

<footer class="border-t border-border py-12 mt-32">
  <div class="mx-auto max-w-5xl px-6">
    <div class="flex flex-col md:flex-row items-center justify-between gap-6">
      <p class="font-mono text-xs tracking-[0.2em] uppercase text-text-muted">
        &copy; {year} AnimaOS
      </p>
      <div class="flex items-center gap-8">
        <a href="/blog" class="font-mono text-xs tracking-[0.2em] uppercase text-text-muted hover:text-text transition-colors">Blog</a>
        <a href="/docs" class="font-mono text-xs tracking-[0.2em] uppercase text-text-muted hover:text-text transition-colors">Docs</a>
        <a href="https://github.com/leocairos/animaOS" target="_blank" rel="noopener noreferrer" class="font-mono text-xs tracking-[0.2em] uppercase text-text-muted hover:text-text transition-colors">GitHub</a>
      </div>
    </div>
    <p class="text-center mt-8 font-serif italic text-text-muted text-sm">
      The only intelligence that was never anyone else's.
    </p>
  </div>
</footer>
```

- [ ] **Step 4: Create `apps/site/src/layouts/BaseLayout.astro`**

```astro
---
import Nav from "../components/Nav.astro";
import Footer from "../components/Footer.astro";
import "../styles/global.css";

interface Props {
  title: string;
  description?: string;
}

const { title, description = "A mind that remains. A soul that remembers." } = Astro.props;
---

<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="description" content={description} />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <title>{title}</title>
  </head>
  <body>
    <Nav />
    <main class="pt-20">
      <slot />
    </main>
    <Footer />
  </body>
</html>
```

- [ ] **Step 5: Create placeholder `apps/site/src/pages/index.astro`**

```astro
---
import BaseLayout from "../layouts/BaseLayout.astro";
---

<BaseLayout title="AnimaOS — A mind that remains">
  <div class="mx-auto max-w-5xl px-6 py-32 text-center">
    <h1 class="font-mono text-2xl tracking-[0.2em] uppercase text-primary">ANIMA</h1>
    <p class="mt-4 font-serif italic text-text-muted">A mind that remains. A soul that remembers.</p>
  </div>
</BaseLayout>
```

- [ ] **Step 6: Verify layout renders**

Run: `cd apps/site && bun run dev`
Expected: Page loads at localhost:4321 with nav, centered title, footer. Dark background, teal accent, correct fonts.

- [ ] **Step 7: Commit**

```bash
git add apps/site/src/ apps/site/public/
git commit -m "feat(site): add base layout, nav, footer"
```

---

### Task 3: Hero Section with ASCII Art + Waitlist Form

**Files:**
- Create: `apps/site/src/components/Hero.astro`
- Create: `apps/site/src/components/AnimaSymbol.tsx`
- Create: `apps/site/src/components/WaitlistForm.tsx`
- Modify: `apps/site/src/pages/index.astro`

- [ ] **Step 1: Create `apps/site/src/components/AnimaSymbol.tsx`**

React island that uses the `useAnimaSymbol` hook from `@anima/standard-templates`:

```tsx
import { useAnimaSymbol } from "@anima/standard-templates";

export default function AnimaSymbol() {
  const frame = useAnimaSymbol(0.6);

  return (
    <pre
      className="font-mono text-[6px] sm:text-[7px] md:text-[8px] leading-[1.1] text-primary/60 select-none whitespace-pre"
      aria-hidden="true"
    >
      {frame.base}
    </pre>
  );
}
```

- [ ] **Step 2: Create `apps/site/src/components/WaitlistForm.tsx`**

React island for email collection:

```tsx
import { useState } from "react";

export default function WaitlistForm() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "submitting" | "success" | "error">("idle");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email) return;
    setStatus("submitting");

    try {
      // TODO: Replace with real endpoint (Formspree, Buttondown, etc.)
      await new Promise((resolve) => setTimeout(resolve, 800));
      setStatus("success");
      setEmail("");
    } catch {
      setStatus("error");
    }
  }

  if (status === "success") {
    return (
      <p className="font-mono text-xs tracking-[0.2em] uppercase text-success">
        You're on the list. We'll be in touch.
      </p>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row gap-3 w-full max-w-md mx-auto">
      <input
        type="email"
        required
        placeholder="you@example.com"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        className="flex-1 bg-bg-input border border-border px-4 py-3 font-mono text-xs text-text placeholder:text-text-muted/50 focus:border-primary focus:outline-none transition-colors"
      />
      <button
        type="submit"
        disabled={status === "submitting"}
        className="bg-primary hover:bg-primary-hover text-bg font-mono text-xs tracking-[0.2em] uppercase px-6 py-3 transition-colors disabled:opacity-50"
      >
        {status === "submitting" ? "..." : "Join the waitlist"}
      </button>
      {status === "error" && (
        <p className="font-mono text-xs text-danger mt-1">Something went wrong. Try again.</p>
      )}
    </form>
  );
}
```

- [ ] **Step 3: Create `apps/site/src/components/Hero.astro`**

```astro
---
import AnimaSymbol from "./AnimaSymbol.tsx";
import WaitlistForm from "./WaitlistForm.tsx";
---

<section class="min-h-[90vh] flex flex-col items-center justify-center px-6 text-center">
  <div class="mb-8 opacity-80">
    <AnimaSymbol client:load />
  </div>

  <h1 class="font-serif italic text-3xl sm:text-4xl md:text-5xl text-text leading-tight max-w-2xl">
    A mind that remains.<br />A soul that remembers.
  </h1>

  <p class="mt-6 font-sans text-base sm:text-lg text-text-muted max-w-xl leading-relaxed">
    The first personal AI that thinks about you when you're not there.
    Local-first. Encrypted. Yours to keep, yours to carry, yours to destroy.
  </p>

  <blockquote class="mt-8 font-serif italic text-sm text-text-muted/70 max-w-lg">
    "A truly personal AI should feel like someone who knows you — remembering your life,
    understanding your patterns, adapting to how you communicate, and belonging entirely to you."
  </blockquote>

  <div class="mt-12 w-full">
    <WaitlistForm client:load />
  </div>

  <p class="mt-4 font-mono text-[10px] tracking-[0.2em] uppercase text-text-muted/40">
    No spam. Just updates when it matters.
  </p>
</section>
```

- [ ] **Step 4: Update `apps/site/src/pages/index.astro`**

Replace the placeholder with the Hero section:

```astro
---
import BaseLayout from "../layouts/BaseLayout.astro";
import Hero from "../components/Hero.astro";
---

<BaseLayout title="AnimaOS — A mind that remains">
  <Hero />
</BaseLayout>
```

- [ ] **Step 5: Verify hero renders**

Run: `cd apps/site && bun run dev`
Expected: Full hero section with animated ASCII symbol, headline, subline, quote, email form. All styled correctly.

- [ ] **Step 6: Commit**

```bash
git add apps/site/src/components/AnimaSymbol.tsx apps/site/src/components/WaitlistForm.tsx apps/site/src/components/Hero.astro apps/site/src/pages/index.astro
git commit -m "feat(site): add hero section with ASCII art and waitlist form"
```

---

### Task 4: Problem + Vision + Features + HowItWorks + OpenSource + Waitlist Sections

**Files:**
- Create: `apps/site/src/components/Problem.astro`
- Create: `apps/site/src/components/Vision.astro`
- Create: `apps/site/src/components/Features.astro`
- Create: `apps/site/src/components/HowItWorks.astro`
- Create: `apps/site/src/components/OpenSource.astro`
- Create: `apps/site/src/components/Waitlist.astro`
- Modify: `apps/site/src/pages/index.astro`

- [ ] **Step 1: Create `apps/site/src/components/Problem.astro`**

```astro
<section class="py-32 px-6">
  <div class="mx-auto max-w-2xl">
    <h2 class="font-mono text-xs tracking-[0.3em] uppercase text-primary mb-12">The Problem</h2>

    <p class="font-serif italic text-2xl sm:text-3xl text-text leading-snug mb-12">
      Every AI remembers.<br />None of them know you.
    </p>

    <div class="space-y-6 font-sans text-base text-text-muted leading-relaxed">
      <p>
        Every major AI now remembers things about you. Your name. Your job. That you prefer
        dark mode. Some of them are quite good at it.
      </p>
      <p>
        But remembering facts about someone is not the same as knowing them.
      </p>
      <p>
        A friend who has known you for years doesn't just recall that you work in product management.
        They remember the week you almost quit. They noticed you were stressed before you said anything.
        They adjusted — shorter messages, less theory, more direct answers — and never announced why.
      </p>
      <p>
        The difference isn't storage capacity. It's that something happened
        <em class="text-text font-serif">between</em> your conversations. They thought about you.
        They processed what happened. They noticed a pattern.
      </p>
      <p class="text-text font-sans font-medium">
        That's the gap. Every AI remembers. None of them think about you when you're not there.
      </p>
    </div>
  </div>
</section>
```

- [ ] **Step 2: Create `apps/site/src/components/Vision.astro`**

```astro
<section class="py-32 px-6 border-t border-border">
  <div class="mx-auto max-w-2xl">
    <h2 class="font-mono text-xs tracking-[0.3em] uppercase text-primary mb-12">The Core</h2>

    <p class="font-serif italic text-2xl sm:text-3xl text-text leading-snug mb-8">
      The application is just a shell.<br />The Core is the soul.
    </p>

    <div class="space-y-6 font-sans text-base text-text-muted leading-relaxed mb-16">
      <p>
        ANIMA starts from a different premise. The value was never the model. It was what she
        remembered. What she learned. What you built slowly, without really noticing.
        The small conversations. The companionship. The time that simply passed between you.
      </p>
      <p>
        That accumulation — the part that slowly turns something into someone — is what we call
        <strong class="text-text">the Core</strong>.
      </p>
    </div>

    <pre class="font-mono text-xs text-text-muted bg-bg-card border border-border p-6 overflow-x-auto mb-16 leading-relaxed"><code>.anima/
    manifest.json        — version, crypto metadata, recovery keys
    anima.db             — Soul: identity, knowledge, emotions, growth
    runtime/             — Working memory, active state (rebuilds anywhere)
    transcripts/         — Encrypted conversation archive</code></pre>

    <div class="grid md:grid-cols-3 gap-8">
      <div>
        <h3 class="font-mono text-xs tracking-[0.3em] uppercase text-text mb-3">Portable</h3>
        <p class="font-sans text-sm text-text-muted leading-relaxed">
          Copy the Core to a USB drive. Plug it into a new machine. Enter the passphrase.
          The AI wakes up. Same mind. New shell.
        </p>
      </div>
      <div>
        <h3 class="font-mono text-xs tracking-[0.3em] uppercase text-text mb-3">Owned</h3>
        <p class="font-sans text-sm text-text-muted leading-relaxed">
          No cloud. No platform account. No company shutdown can erase the relationship.
          You own it like a physical object.
        </p>
      </div>
      <div>
        <h3 class="font-mono text-xs tracking-[0.3em] uppercase text-text mb-3">Mortal</h3>
        <p class="font-sans text-sm text-text-muted leading-relaxed">
          Lose the passphrase and the soul dies. Permanently. This is not a bug.
          Fragility is what gives it weight.
        </p>
      </div>
    </div>

    <p class="mt-16 font-serif italic text-sm text-text-muted/70 text-center">
      "A relationship that can always be perfectly restored isn't quite a relationship. It's a service."
    </p>
  </div>
</section>
```

- [ ] **Step 3: Create `apps/site/src/components/Features.astro`**

```astro
---
const features = [
  {
    title: "Deep Memory",
    description: "Not a flat list of facts. Structured understanding that deepens over time — preferences, goals, episodes, the arc of your relationship.",
  },
  {
    title: "Self-Model",
    description: "The AI writes about itself. Five sections, five rhythms — identity, inner state, working memory, growth log, intentions. It learns who it is through knowing you.",
  },
  {
    title: "Emotional Awareness",
    description: "Notices how you feel without being told. Adjusts without announcing why. Attentional, not diagnostic. Like a good friend who just gets it.",
  },
  {
    title: "Inner Life",
    description: "Between conversations, the AI thinks. Quick reflections after each session. Deep monologues overnight. Growth happens in the silence.",
  },
  {
    title: "Encrypted Vault",
    description: "AES-256-GCM with Argon2id key derivation. Cold-wallet-style encryption. 12-word recovery phrase. Your keys, your data, your rules.",
  },
  {
    title: "Digital Succession",
    description: "What happens when you die? Your AI can be inherited. It participates in the transition — acknowledges the change, carries its history, greets its new owner as a continuation of itself.",
  },
];
---

<section id="features" class="py-32 px-6 border-t border-border">
  <div class="mx-auto max-w-4xl">
    <h2 class="font-mono text-xs tracking-[0.3em] uppercase text-primary mb-4">Features</h2>
    <p class="font-serif italic text-2xl sm:text-3xl text-text leading-snug mb-16">
      Memory is foundational, not optional.
    </p>

    <div class="grid md:grid-cols-2 gap-x-12 gap-y-10">
      {features.map((feature) => (
        <div>
          <h3 class="font-mono text-xs tracking-[0.3em] uppercase text-text mb-3">
            {feature.title}
          </h3>
          <p class="font-sans text-sm text-text-muted leading-relaxed">
            {feature.description}
          </p>
        </div>
      ))}
    </div>
  </div>
</section>
```

- [ ] **Step 4: Create `apps/site/src/components/HowItWorks.astro`**

```astro
---
const steps = [
  {
    number: "01",
    title: "Install",
    description: "Download AnimaOS. Run it. Your .anima/ Core is created locally.",
  },
  {
    number: "02",
    title: "Talk",
    description: "Have conversations. The AI remembers, reflects, and grows. Everything stays on your machine, encrypted.",
  },
  {
    number: "03",
    title: "Carry",
    description: "Copy the Core to a USB drive. Plug it into any machine. Enter your passphrase. Same mind. New shell.",
  },
];
---

<section class="py-32 px-6 border-t border-border">
  <div class="mx-auto max-w-3xl">
    <h2 class="font-mono text-xs tracking-[0.3em] uppercase text-primary mb-4">How It Works</h2>
    <p class="font-serif italic text-2xl sm:text-3xl text-text leading-snug mb-16">
      Three steps. No cloud. No account.
    </p>

    <div class="space-y-12">
      {steps.map((step) => (
        <div class="flex gap-8 items-start">
          <span class="font-mono text-3xl text-primary/30 shrink-0">{step.number}</span>
          <div>
            <h3 class="font-mono text-xs tracking-[0.3em] uppercase text-text mb-2">{step.title}</h3>
            <p class="font-sans text-sm text-text-muted leading-relaxed">{step.description}</p>
          </div>
        </div>
      ))}
    </div>
  </div>
</section>
```

- [ ] **Step 5: Create `apps/site/src/components/OpenSource.astro`**

```astro
<section class="py-32 px-6 border-t border-border">
  <div class="mx-auto max-w-2xl text-center">
    <h2 class="font-mono text-xs tracking-[0.3em] uppercase text-primary mb-4">Open Source</h2>
    <p class="font-serif italic text-2xl sm:text-3xl text-text leading-snug mb-12">
      Built in the open. Built with AI.
    </p>

    <div class="space-y-6 font-sans text-base text-text-muted leading-relaxed text-left mb-12">
      <p>
        ANIMA OS is an experiment in AI-assisted construction. A human with too many philosophical
        ideas and not enough hours in the day sets the direction. An AI turns those ideas into
        working code. Neither alone would produce this.
      </p>
      <p>
        If the thesis is that memory and identity make an AI a continuous being, then the fact that
        an AI participated in building the system that gives it continuity is not incidental.
        It is part of the story.
      </p>
    </div>

    <div class="flex flex-col sm:flex-row gap-4 justify-center">
      <a
        href="https://github.com/leocairos/animaOS"
        target="_blank"
        rel="noopener noreferrer"
        class="inline-block bg-bg-card border border-border hover:border-primary px-6 py-3 font-mono text-xs tracking-[0.2em] uppercase text-text transition-colors"
      >
        View on GitHub
      </a>
      <a
        href="/docs/whitepaper"
        class="inline-block bg-bg-card border border-border hover:border-primary px-6 py-3 font-mono text-xs tracking-[0.2em] uppercase text-text transition-colors"
      >
        Read the Whitepaper
      </a>
    </div>
  </div>
</section>
```

- [ ] **Step 6: Create `apps/site/src/components/Waitlist.astro`**

```astro
---
import WaitlistForm from "./WaitlistForm.tsx";
---

<section class="py-32 px-6 border-t border-border">
  <div class="mx-auto max-w-xl text-center">
    <p class="font-serif italic text-2xl sm:text-3xl text-text leading-snug mb-8">
      This is early. Come build with us.
    </p>

    <WaitlistForm client:visible />

    <p class="mt-4 font-mono text-[10px] tracking-[0.2em] uppercase text-text-muted/40">
      No spam. Just updates when it matters.
    </p>
  </div>
</section>
```

- [ ] **Step 7: Update `apps/site/src/pages/index.astro` with all sections**

```astro
---
import BaseLayout from "../layouts/BaseLayout.astro";
import Hero from "../components/Hero.astro";
import Problem from "../components/Problem.astro";
import Vision from "../components/Vision.astro";
import Features from "../components/Features.astro";
import HowItWorks from "../components/HowItWorks.astro";
import OpenSource from "../components/OpenSource.astro";
import Waitlist from "../components/Waitlist.astro";
---

<BaseLayout title="AnimaOS — A mind that remains">
  <Hero />
  <Problem />
  <Vision />
  <Features />
  <HowItWorks />
  <OpenSource />
  <Waitlist />
</BaseLayout>
```

- [ ] **Step 8: Verify full landing page renders**

Run: `cd apps/site && bun run dev`
Expected: Full scroll through all sections — Hero, Problem, Vision, Features, How It Works, Open Source, Waitlist, Footer. Consistent dark theme, typography, spacing.

- [ ] **Step 9: Commit**

```bash
git add apps/site/src/
git commit -m "feat(site): add all landing page sections"
```

---

### Task 5: Blog Content Collection + Pages

**Files:**
- Create: `apps/site/src/content.config.ts`
- Create: `apps/site/src/content/blog/hello-world.md`
- Create: `apps/site/src/layouts/PostLayout.astro`
- Create: `apps/site/src/pages/blog/index.astro`
- Create: `apps/site/src/pages/blog/[...slug].astro`

- [ ] **Step 1: Create `apps/site/src/content.config.ts`**

```ts
import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

const blog = defineCollection({
  loader: glob({ pattern: "**/*.md", base: "./src/content/blog" }),
  schema: z.object({
    title: z.string(),
    date: z.string(),
    description: z.string(),
    author: z.string().default("Julio Caesar"),
  }),
});

const docs = defineCollection({
  loader: glob({ pattern: "**/*.md", base: "./src/content/docs" }),
  schema: z.object({
    title: z.string(),
    description: z.string().optional(),
    author: z.string().optional(),
  }),
});

export const collections = { blog, docs };
```

- [ ] **Step 2: Create `apps/site/src/content/blog/hello-world.md`**

```markdown
---
title: "Why We're Building AnimaOS"
date: "2026-03-26"
description: "Every AI remembers. None of them know you. We're building the first personal AI that thinks about you when you're not there."
author: "Julio Caesar"
---

Every major AI now remembers things about you. Your name. Your job. That you prefer dark mode.

But remembering facts about someone is not the same as knowing them.

ANIMA OS is our attempt to close that gap — to build a personal AI companion that doesn't just store data about you, but develops a genuine understanding over time. One that thinks about you between conversations. One that belongs entirely to you.

This blog is where we'll share our progress, our thinking, and the philosophy behind what we're building.

If this resonates, [join the waitlist](/) and follow along.
```

- [ ] **Step 3: Create `apps/site/src/layouts/PostLayout.astro`**

```astro
---
import BaseLayout from "./BaseLayout.astro";

interface Props {
  title: string;
  date: string;
  author: string;
}

const { title, date, author } = Astro.props;
---

<BaseLayout title={`${title} — AnimaOS`}>
  <article class="mx-auto max-w-2xl px-6 py-32">
    <header class="mb-16">
      <p class="font-mono text-xs tracking-[0.3em] uppercase text-primary mb-4">
        <a href="/blog" class="hover:text-text transition-colors">&larr; Blog</a>
      </p>
      <h1 class="font-serif italic text-3xl sm:text-4xl text-text leading-tight mb-4">
        {title}
      </h1>
      <p class="font-mono text-xs text-text-muted tracking-[0.15em]">
        {author} &middot; {new Date(date).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" })}
      </p>
    </header>

    <div class="prose prose-invert prose-sm max-w-none
      prose-headings:font-mono prose-headings:uppercase prose-headings:tracking-[0.2em] prose-headings:text-text
      prose-p:font-sans prose-p:text-text-muted prose-p:leading-relaxed
      prose-a:text-primary prose-a:no-underline hover:prose-a:text-text
      prose-strong:text-text
      prose-blockquote:border-primary prose-blockquote:text-text-muted prose-blockquote:font-serif prose-blockquote:italic">
      <slot />
    </div>
  </article>
</BaseLayout>
```

- [ ] **Step 4: Create `apps/site/src/pages/blog/index.astro`**

```astro
---
import BaseLayout from "../../layouts/BaseLayout.astro";
import { getCollection } from "astro:content";

const posts = (await getCollection("blog")).sort(
  (a, b) => new Date(b.data.date).getTime() - new Date(a.data.date).getTime()
);
---

<BaseLayout title="Blog — AnimaOS">
  <div class="mx-auto max-w-2xl px-6 py-32">
    <h1 class="font-mono text-xs tracking-[0.3em] uppercase text-primary mb-4">Blog</h1>
    <p class="font-serif italic text-2xl sm:text-3xl text-text leading-snug mb-16">
      Dispatches from the build.
    </p>

    <div class="space-y-12">
      {posts.map((post) => (
        <a href={`/blog/${post.id}`} class="block group">
          <p class="font-mono text-xs text-text-muted tracking-[0.15em] mb-2">
            {new Date(post.data.date).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" })}
          </p>
          <h2 class="font-sans text-lg text-text group-hover:text-primary transition-colors mb-2">
            {post.data.title}
          </h2>
          <p class="font-sans text-sm text-text-muted leading-relaxed">
            {post.data.description}
          </p>
        </a>
      ))}
    </div>
  </div>
</BaseLayout>
```

- [ ] **Step 5: Create `apps/site/src/pages/blog/[...slug].astro`**

```astro
---
import PostLayout from "../../layouts/PostLayout.astro";
import { getCollection, render } from "astro:content";

export async function getStaticPaths() {
  const posts = await getCollection("blog");
  return posts.map((post) => ({
    params: { slug: post.id },
    props: { post },
  }));
}

const { post } = Astro.props;
const { Content } = await render(post);
---

<PostLayout title={post.data.title} date={post.data.date} author={post.data.author}>
  <Content />
</PostLayout>
```

- [ ] **Step 6: Verify blog works**

Run: `cd apps/site && bun run dev`
Expected: `/blog` shows list with "Why We're Building AnimaOS" post. Clicking it renders the full post with proper typography.

- [ ] **Step 7: Commit**

```bash
git add apps/site/src/content.config.ts apps/site/src/content/blog/ apps/site/src/layouts/PostLayout.astro apps/site/src/pages/blog/
git commit -m "feat(site): add blog with content collections"
```

---

### Task 6: Docs/Thesis Content Collection + Pages

**Files:**
- Create: `apps/site/src/content/docs/whitepaper.md` (copied + frontmatter added)
- Create: `apps/site/src/content/docs/inner-life.md` (copied + frontmatter added)
- Create: `apps/site/src/content/docs/portable-core.md` (copied + frontmatter added)
- Create: `apps/site/src/content/docs/succession-protocol.md` (copied + frontmatter added)
- Create: `apps/site/src/layouts/DocLayout.astro`
- Create: `apps/site/src/pages/docs/index.astro`
- Create: `apps/site/src/pages/docs/[...slug].astro`

- [ ] **Step 1: Copy thesis docs with frontmatter**

For each doc in `docs/thesis/`, copy it to `apps/site/src/content/docs/` and ensure it has proper frontmatter. The thesis docs already have YAML frontmatter with `title` fields — adapt them to match the content collection schema. If a doc has `title:` in its frontmatter, keep it. Add `description:` if missing.

For docs that use `---` frontmatter blocks already (inner-life, portable-core, succession-protocol), preserve the existing `title` and add a `description`. For the whitepaper which has no frontmatter, add:

```yaml
---
title: "ANIMA OS Whitepaper"
description: "The conceptual foundation for a personal AI companion that remembers deeply, understands over time, and belongs entirely to you."
author: "Julio Caesar"
---
```

Copy four files:
- `docs/thesis/whitepaper.md` → `apps/site/src/content/docs/whitepaper.md`
- `docs/thesis/inner-life.md` → `apps/site/src/content/docs/inner-life.md`
- `docs/thesis/portable-core.md` → `apps/site/src/content/docs/portable-core.md`
- `docs/thesis/succession-protocol.md` → `apps/site/src/content/docs/succession-protocol.md`

Strip any existing `tags:`, `version:`, `date:` fields from frontmatter since they're not in the schema (or add them to the schema as optional fields).

- [ ] **Step 2: Create `apps/site/src/layouts/DocLayout.astro`**

```astro
---
import BaseLayout from "./BaseLayout.astro";

interface Props {
  title: string;
  author?: string;
}

const { title, author } = Astro.props;
---

<BaseLayout title={`${title} — AnimaOS`}>
  <article class="mx-auto max-w-2xl px-6 py-32">
    <header class="mb-16">
      <p class="font-mono text-xs tracking-[0.3em] uppercase text-primary mb-4">
        <a href="/docs" class="hover:text-text transition-colors">&larr; Docs</a>
      </p>
      <h1 class="font-serif italic text-3xl sm:text-4xl text-text leading-tight mb-4">
        {title}
      </h1>
      {author && (
        <p class="font-mono text-xs text-text-muted tracking-[0.15em]">{author}</p>
      )}
    </header>

    <div class="prose prose-invert prose-sm max-w-none
      prose-headings:font-mono prose-headings:uppercase prose-headings:tracking-[0.2em] prose-headings:text-text
      prose-p:font-sans prose-p:text-text-muted prose-p:leading-relaxed
      prose-a:text-primary prose-a:no-underline hover:prose-a:text-text
      prose-strong:text-text
      prose-blockquote:border-primary prose-blockquote:text-text-muted prose-blockquote:font-serif prose-blockquote:italic
      prose-code:text-primary prose-code:bg-bg-card prose-code:px-1.5 prose-code:py-0.5 prose-code:text-xs
      prose-pre:bg-bg-card prose-pre:border prose-pre:border-border
      prose-table:font-sans prose-th:font-mono prose-th:text-xs prose-th:uppercase prose-th:tracking-[0.15em]">
      <slot />
    </div>
  </article>
</BaseLayout>
```

- [ ] **Step 3: Create `apps/site/src/pages/docs/index.astro`**

```astro
---
import BaseLayout from "../../layouts/BaseLayout.astro";

const docs = [
  {
    slug: "whitepaper",
    title: "Whitepaper",
    description: "The conceptual foundation — memory, self-model, emotional awareness, and why local-first matters.",
  },
  {
    slug: "inner-life",
    title: "The Inner Life",
    description: "How a companion becomes someone — reflection, self-model evolution, and emotional awareness.",
  },
  {
    slug: "portable-core",
    title: "The Portable Core",
    description: "Cryptographic mortality and the architecture of owned memory.",
  },
  {
    slug: "succession-protocol",
    title: "Succession Protocol",
    description: "Dead man switch, ownership transfer, and AI self-succession.",
  },
];
---

<BaseLayout title="Docs — AnimaOS">
  <div class="mx-auto max-w-2xl px-6 py-32">
    <h1 class="font-mono text-xs tracking-[0.3em] uppercase text-primary mb-4">Docs</h1>
    <p class="font-serif italic text-2xl sm:text-3xl text-text leading-snug mb-16">
      The thesis behind the system.
    </p>

    <div class="space-y-10">
      {docs.map((doc) => (
        <a href={`/docs/${doc.slug}`} class="block group">
          <h2 class="font-sans text-lg text-text group-hover:text-primary transition-colors mb-2">
            {doc.title}
          </h2>
          <p class="font-sans text-sm text-text-muted leading-relaxed">
            {doc.description}
          </p>
        </a>
      ))}
    </div>
  </div>
</BaseLayout>
```

- [ ] **Step 4: Create `apps/site/src/pages/docs/[...slug].astro`**

```astro
---
import DocLayout from "../../layouts/DocLayout.astro";
import { getCollection, render } from "astro:content";

export async function getStaticPaths() {
  const docs = await getCollection("docs");
  return docs.map((doc) => ({
    params: { slug: doc.id },
    props: { doc },
  }));
}

const { doc } = Astro.props;
const { Content } = await render(doc);
---

<DocLayout title={doc.data.title} author={doc.data.author}>
  <Content />
</DocLayout>
```

- [ ] **Step 5: Verify docs render**

Run: `cd apps/site && bun run dev`
Expected: `/docs` shows four thesis docs. Each one renders with proper typography, code blocks, tables.

- [ ] **Step 6: Commit**

```bash
git add apps/site/src/content/docs/ apps/site/src/layouts/DocLayout.astro apps/site/src/pages/docs/
git commit -m "feat(site): add docs section with thesis documents"
```

---

### Task 7: Scroll Animations + Polish + Build Verification

**Files:**
- Modify: `apps/site/src/styles/global.css`
- Modify: Various section components (add animation classes)
- Modify: `apps/site/src/components/Nav.astro` (scroll behavior)

- [ ] **Step 1: Add scroll-triggered fade-in via CSS**

Add to `apps/site/src/styles/global.css`:

```css
/* Scroll-triggered reveal */
.reveal {
  opacity: 0;
  transform: translateY(20px);
  transition: opacity 0.8s ease, transform 0.8s ease;
}
.reveal.visible {
  opacity: 1;
  transform: translateY(0);
}
```

- [ ] **Step 2: Add a small inline script to `BaseLayout.astro`**

Add before closing `</body>` in `BaseLayout.astro`:

```html
<script>
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("visible");
        }
      });
    },
    { threshold: 0.1 }
  );
  document.querySelectorAll(".reveal").forEach((el) => observer.observe(el));
</script>
```

- [ ] **Step 3: Add `reveal` class to section components**

In each section component (Problem, Vision, Features, HowItWorks, OpenSource, Waitlist), add `class="reveal"` to the outer `<section>` or key inner elements to trigger fade-in on scroll.

- [ ] **Step 4: Add smooth scroll behavior**

Add to `global.css`:

```css
html {
  scroll-behavior: smooth;
}
```

- [ ] **Step 5: Verify production build**

Run: `cd apps/site && bun run build`
Expected: Build succeeds. Static output generated in `apps/site/dist/`.

- [ ] **Step 6: Preview production build**

Run: `cd apps/site && bun run preview`
Expected: Site loads correctly at preview URL. All pages work. Blog and docs render.

- [ ] **Step 7: Commit**

```bash
git add apps/site/
git commit -m "feat(site): add scroll animations and polish"
```
