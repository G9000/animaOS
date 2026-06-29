---
title: Presence Core Module
description: Optional ambient awareness and proactive behavior for ANIMA
category: architecture
updated: 2026-06-29
---

# Presence Core Module

[Back to Capability Modules](README.md)

`presence.core` lets ANIMA feel present between explicit prompts.

It answers:

```text
When is ANIMA allowed to notice, wait, nudge, remind, or prepare?
```

Presence is subtle. Done well, it feels like continuity. Done poorly, it feels intrusive. That makes it a governed capability, not a background convenience.

## Capability Id

```text
presence.core
```

## What Presence Owns

Presence Core owns:

- availability state
- quiet hours
- notification intensity
- proactive nudge policy
- check-in scheduling
- idle and activity signals
- daily greeting context
- follow-up timers
- presence audit events
- ambient context summaries

Presence Core does not own:

- raw screen/camera/audio surveillance
- external calendar integration directly
- durable memory writes
- unsolicited high-risk action execution
- constant interruption

## Presence Is Attention, Not Surveillance

Presence should not mean ANIMA watches everything.

It should mean ANIMA can maintain lightweight awareness of:

- whether the user is active or away
- whether a promised follow-up is due
- whether the user configured quiet hours
- whether an open task deserves a gentle nudge
- whether background reflection should run

Presence can consume summaries from other modules, but it should not bypass their consent rules.

## Proactive Levels

| Level | Meaning | Example |
| --- | --- | --- |
| Silent preparation | ANIMA prepares context without interrupting | preloads memory for morning brief |
| Passive surfacing | UI shows a pending item | badge or queue item |
| Gentle nudge | ANIMA sends a low-friction prompt | "Want me to keep holding this for later?" |
| Scheduled follow-up | User-approved reminder/check-in | "Check in tomorrow afternoon" |
| Autonomous action | ANIMA acts without immediate prompt | out of scope without explicit policy |

Presence should start with silent preparation, passive surfacing, and user-approved follow-ups.

## Sources

Presence may use:

- chat activity
- task state
- memory working notes
- explicit reminders
- local app foreground state if user enables it
- calendar summaries from external integrations
- desktop idle state if user enables it

Each source should retain its own module or integration boundary.

Example:

```text
Google Calendar data comes from apps/anima-mod/google.
Presence Core consumes a policy-approved availability summary.
Presence Core does not become the Google integration itself.
```

## Tools

Possible tools:

- `schedule_follow_up`
- `check_presence_state`
- `set_quiet_hours`
- `create_nudge_candidate`

Presence tools should be careful with interruption. A nudge candidate can be queued for UI review instead of immediately interrupting the user.

## Memory Boundary

Presence produces patterns over time.

Good memory candidate:

```text
User prefers gentle follow-ups in the late afternoon, not early morning.
```

Bad memory candidate:

```text
User was idle at 2:17 PM.
```

Raw activity signals should stay runtime-only. Stable preferences can become memory candidates.

## User Controls

Presence needs excellent controls:

- enabled/disabled
- quiet hours
- nudge frequency
- allowed channels
- allowed signal sources
- reminder behavior
- proactive mode
- history/audit visibility

The user should always be able to make ANIMA quieter.

## Failure Cases

Expected failures:

- quiet hours active
- notifications disabled
- source integration unavailable
- user locked
- follow-up expired
- nudge suppressed by policy
- stale context

The correct failure mode is often silence.

## Future Extensions

Future presence can include:

- daily brief preparation
- context-aware return greetings
- project momentum nudges
- reflection scheduling
- multi-device availability
- local daemon integration
- presence-aware voice greetings
