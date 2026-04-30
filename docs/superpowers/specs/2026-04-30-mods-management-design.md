# Mods Management Design

## Goal

Improve the mods management layer end to end: make the anima-mod management backend build cleanly, return stable lifecycle data, and expose the missing management controls in the desktop UI.

## Scope

This pass covers the existing `apps/anima-mod` management API and the existing desktop Mods pages. It does not introduce a marketplace, dependency resolver, permissions model, or a new mod packaging format.

## Backend Design

The management API remains the source of truth for mod list/detail/config/lifecycle/install/uninstall/tool metadata. The change is to make its data contracts explicit and stable:

- `StateService` returns normalized `ModState` objects, not nullable Drizzle row shapes.
- `EventService` accepts only known lifecycle/config event types.
- Lifecycle endpoints return the refreshed state after enable, disable, or restart.
- Detail responses include recent events.
- A dedicated `GET /api/mods/:id/events` endpoint returns recent mod events.
- Drizzle DB typing is fixed at the DB boundary so `bun run build:anima-mod` can pass.

## Desktop Design

The desktop keeps the existing `/mods` and `/mods/:id` routes, but makes them operationally useful:

- Mods list adds search, status filters, status counts, refresh, and per-mod pending/error feedback.
- Mod cards show whether config/setup/tools are present.
- Mod detail exposes enable, disable, restart, uninstall, refresh, health, config save status/errors, and recent events.
- Built-in mods should not show an active uninstall command; uninstall failures are surfaced plainly if the backend rejects them.

## Data Flow

Desktop calls `mod-client.ts`, which wraps the management API. The management router delegates lifecycle work to `ModRegistry`, persists state and events through management services, broadcasts websocket status events, and returns normalized payloads. Desktop websocket events trigger refreshes, while direct action handlers also refresh after successful mutations.

## Error Handling

Backend route errors should continue returning parseable API errors. Desktop should keep the current "anima-mod not running" state for connection failures and add inline action/config errors where the user initiated the operation.

## Testing

- Add anima-mod management tests for normalized state, event listing, lifecycle endpoint payloads, and uninstall behavior.
- Add desktop mod client tests for `getModEvents()` and `uninstallMod()`.
- Run `bun run test:anima-mod`.
- Run `bun run build:anima-mod`.
- Run desktop type/build verification.

