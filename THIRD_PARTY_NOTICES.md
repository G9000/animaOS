# Third-Party Notices

animaOS includes selectively adapted implementation patterns from OpenAI Codex.

## OpenAI Codex

- Upstream: `https://github.com/openai/codex`
- Audited commit: `9e552e9d15ba52bed7077d5357f3e18e330f8f38`
- License: Apache License 2.0; full text at `third_party/licenses/Apache-2.0.txt`
- Upstream NOTICE: `third_party/notices/openai-codex-NOTICE.txt`

Adaptation inventory:

| Upstream path | Local destination | Modification summary |
| --- | --- | --- |
| `codex-rs/file-system/src/lib.rs` | `packages/anima-file-tools/src/limits.rs` | Reworked bounded defaults into validated backend-neutral operation limits. |
| `codex-rs/file-system/src/lib.rs` | `packages/anima-file-tools/src/read.rs` | Reworked bounded chunk reads around explicit HostFS/CoreFS backend handles and cancellation. |
| `codex-rs/file-system/src/lib.rs` | `packages/anima-file-tools/src/walk.rs` | Reworked bounded traversal into deterministic backend pagination without host sandbox assumptions. |
| `codex-rs/file-system/src/lib.rs` | `packages/anima-file-tools/src/search.rs` | Reworked bounded output patterns into streaming literal/linear-regex grep with typed skips. |
| `codex-rs/file-system/src/lib.rs` | `packages/anima-file-tools/src/text.rs` | Reworked bounded reads into line-window text output with explicit binary and UTF-8 failures. |
| `codex-rs/apply-patch/src/parser.rs` | `packages/anima-file-tools/src/patch/parser.rs` | Reworked the patch grammar into backend-neutral relative paths and typed operations. |
| `codex-rs/apply-patch/src/seek_sequence.rs` | `packages/anima-file-tools/src/patch/planner.rs` | Reworked ordered sequence matching into a preflight-only mutation planner with declared backend atomicity. |

No runtime or build dependency points at the upstream Codex checkout. The adapted
files retain Apache-2.0 SPDX headers and commit-pinned attribution comments.
