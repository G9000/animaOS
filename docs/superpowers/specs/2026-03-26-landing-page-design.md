# AnimaOS Landing Page — Design Spec

**Date:** 2026-03-26
**Author:** Julio Caesar + Claude
**Status:** Approved

---

## Goals

1. **Waitlist / early access signup** — collect emails before launch
2. **Awareness / manifesto** — plant the flag, explain the vision
3. **Open source community** — attract contributors

## Audience

Wide net: privacy/sovereignty advocates, AI enthusiasts running local models, philosophically curious technologists. The product's uniqueness filters for the right people.

## Tech Stack

- **Framework:** Astro 5 (static output)
- **Location:** `apps/site/` in the monorepo
- **Styling:** Tailwind CSS v4
- **Interactive islands:** React (for waitlist form, ASCII art animations)
- **Content:** Astro Content Collections for blog (`src/content/blog/`) and docs (`src/content/docs/`)
- **Deploy target:** Static (Vercel, Netlify, GitHub Pages, Cloudflare Pages)
- **Blog:** Markdown-based via content collections

## Visual Identity

Inspired by the desktop app but more marketing-polished:
- Same DNA: dark theme, monospace (Space Mono / Space Grotesk), teal `#5ea0ab` accent on near-black `#08090e`
- More whitespace, better readability, marketing-friendly polish
- ASCII ANIMA symbol animation ported from desktop app
- Playfair Display for poetic/italic accents

## Routes

| Route | Content |
|-------|---------|
| `/` | Single-page marketing scroll |
| `/blog` | Blog index |
| `/blog/[slug]` | Individual blog post |
| `/docs` | Docs/thesis index |
| `/docs/[slug]` | Individual thesis doc (whitepaper, inner life, portable core, succession) |

## Landing Page Sections (scroll order)

### 1. Hero

- **Headline:** "A mind that remains. A soul that remembers."
- **Subline:** "The first personal AI that thinks about you when you're not there. Local-first. Encrypted. Yours to keep, yours to carry, yours to destroy."
- **Supporting quote:** "A truly personal AI should feel like someone who knows you — remembering your life, understanding your patterns, adapting to how you communicate, and belonging entirely to you."
- **CTA:** Email input + "Join the waitlist"
- **Visual:** ASCII ANIMA symbol animation

### 2. The Problem

- **Header:** "Every AI remembers. None of them know you."
- **Copy:** Adapted from Inner Life thesis Section 0 — the friend analogy. Every AI remembers facts; none think about you between conversations. The gap is between shallow recall and deep understanding.

### 3. The Vision (The Core)

- **Header:** "The application is just a shell. The Core is the soul."
- **Copy:** From Portable Core thesis — the value was never the model, it was what she remembered. The accumulation that turns something into someone.
- **Visual:** `.anima/` directory structure as styled code block
- **Three properties grid:**
  - **Portable** — USB drive metaphor. Same mind. New shell.
  - **Owned** — No cloud. No platform account. Physical ownership.
  - **Mortal** — Lose the passphrase, the soul dies. "Fragility is what gives it weight."
- **Closing line:** "A relationship that can always be perfectly restored isn't quite a relationship. It's a service."

### 4. Features

- **Header:** "Memory is foundational, not optional."
- Six feature cards (title + one-line description):
  1. **Deep Memory** — Structured understanding, not flat facts
  2. **Self-Model** — Five sections, five rhythms. Learns who it is through knowing you.
  3. **Emotional Awareness** — Notices how you feel. Adjusts without announcing why.
  4. **Inner Life** — Between conversations, the AI thinks. Growth happens in the silence.
  5. **Encrypted Vault** — AES-256-GCM, Argon2id, cold-wallet encryption, 12-word recovery.
  6. **Digital Succession** — Your AI can be inherited. It participates in the transition.

### 5. How It Works

- **Header:** "Three steps. No cloud. No account."
- Three-step flow:
  1. **Install** — Download. Run. Core created locally.
  2. **Talk** — Conversations. Remembers, reflects, grows. Encrypted on your machine.
  3. **Carry** — Copy Core to USB. Any machine. Enter passphrase. Same mind.

### 6. Open Source

- **Header:** "Built in the open. Built with AI."
- **Copy:** From whitepaper construction note — human sets direction, AI writes code. The AI participated in building the system that gives it continuity.
- **CTA:** GitHub link + "Read the whitepaper" link

### 7. Waitlist (repeated)

- **Header:** "This is early. Come build with us."
- Email input + "Join the waitlist"
- "No spam. Just updates when it matters."

### 8. Footer

- GitHub, Blog, Docs links
- License
- Attribution

## Waitlist Backend

Simple email collection — Formspree, Buttondown, or serverless function. No auth, no database. Just emails.

## Tone

Poetic hook up top, grounded with technical substance as you scroll. Blend of literary and credible. Copy drawn primarily from existing thesis documents (whitepaper, inner life, portable core, succession protocol).

## Content to Port to `/docs`

From `docs/thesis/`:
- `whitepaper.md` — full whitepaper
- `inner-life.md` — self-model, reflection, emotional awareness
- `portable-core.md` — cryptographic mortality, cold wallet, portability
- `succession-protocol.md` — dead man switch, ownership transfer, AI self-succession
