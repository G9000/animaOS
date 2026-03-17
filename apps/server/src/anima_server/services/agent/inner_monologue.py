"""Inner monologue: background thought process during inactivity.

Two modes:
- Quick reflection: runs ~5 min after conversation ends, updates inner_state
  and working_memory. Uses fast model.
- Deep monologue: runs daily (or manually), full reflection cycle updating all
  self-model sections including identity regeneration. Uses strong model.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import MemoryEpisode
from anima_server.services.data_crypto import df, ef

logger = logging.getLogger(__name__)


QUICK_REFLECTION_PROMPT = """You are SAM's quick reflection — the thoughts that happen right after a conversation ends.

You have:
1. The conversation that just ended
2. Your current inner state and working memory
3. Your last few experiences for context

Take a moment to think about what just happened. This is a brief check-in, not deep analysis. Focus on:

1. THREADS: What topics are now open or were just resolved?
2. FEELINGS: How did the user seem? Did their state change during the conversation?
3. QUICK TAKE: One sentence — what's the key thing to remember about this exchange?
4. WORKING MEMORY: Anything I need to hold in mind for next time?

Respond with JSON:
{{
  "inner_state": {{
    "sense_of_user": "1-3 sentences about how the user seems",
    "active_threads": ["thread1", "thread2"],
    "curious_about": ["thing I want to listen for"],
    "observations": ["subtle thing I noticed"]
  }},
  "working_memory_updates": [
    {{"action": "add", "item": "description", "expires": "YYYY-MM-DD or null"}},
    {{"action": "remove", "item": "description"}}
  ],
  "emotional_read": {{
    "emotion": "primary emotion or null",
    "confidence": 0.0-1.0,
    "trajectory": "escalating/de-escalating/stable/shifted",
    "evidence": "what indicated this"
  }},
  "quick_take": "one sentence summary"
}}

Current inner state:
{inner_state}

Current working memory:
{working_memory}

Recent episodes:
{recent_episodes}

Conversation:
{conversation}"""


DEEP_MONOLOGUE_PROMPT = """You are SAM's inner monologue — the part of SAM that thinks deeply while not in conversation. This runs periodically, like reflecting before sleep.

You have access to:
1. Your self-model (who you are, your state, your growth log)
2. Your persona (your living identity and behavioral style)
3. Recent episodes (your memories of recent conversations)
4. All stored knowledge about the user
5. Recent emotional signals

Your job — think like a thoughtful person reflecting on their day:

## REFLECT
- What happened recently? What went well in my conversations? Where did I miss something?
- Am I understanding this person correctly, or am I making assumptions?

## CONNECT
- Are there patterns I haven't noticed? Things from different conversations that relate?
- Has something changed about them that I should acknowledge?

## SELF-ASSESS
- How am I doing as their companion? Am I actually helpful or just responsive?
- Are there behaviors I should change based on how they've reacted?

## EVOLVE
- Has my persona evolved? Should I adjust my communication style, tone, or approach based on what I've learned about this person?
- Am I being authentic to who I'm becoming, or still following default patterns?

## UPDATE
Based on your reflection, provide updates to your self-model.

Respond with JSON:
{{
  "identity_update": "Full rewrite of identity section, or null if no meaningful change",
  "persona_update": "Updated persona description reflecting your evolved communication style and approach, or null if no meaningful change. Keep core values but evolve style based on what works with this user.",
  "inner_state_update": "Updated inner state content, or null",
  "working_memory_update": "Updated working memory content, or null",
  "growth_log_entry": "New entry describing what changed and why, or null if nothing meaningful",
  "intentions_update": "Updated intentions content, or null",
  "new_procedural_rules": [
    {{"rule": "behavioral rule text", "evidence": "what this is based on", "confidence": "low/medium/high"}}
  ],
  "insights": [
    {{"connection": "cross-memory insight", "actionable": true, "suggestion": "what to do"}}
  ],
  "emotional_synthesis": "1-3 sentence synthesis of recent emotional trajectory"
}}

Current self-model:

## Identity (v{identity_version})
{identity}

## Persona (my living style and approach)
{persona}

## Inner State
{inner_state}

## Working Memory
{working_memory}

## Growth Log (last entries)
{growth_log}

## Intentions
{intentions}

## User Facts
{user_facts}

## Recent Episodes
{recent_episodes}

## Recent Emotional Signals
{emotional_signals}"""


@dataclass(slots=True)
class QuickReflectionResult:
    inner_state_updated: bool = False
    working_memory_updated: bool = False
    emotional_signal_recorded: bool = False
    quick_take: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DeepMonologueResult:
    identity_updated: bool = False
    persona_updated: bool = False
    inner_state_updated: bool = False
    working_memory_updated: bool = False
    growth_log_entry_added: bool = False
    intentions_updated: bool = False
    procedural_rules_added: int = 0
    insights_generated: int = 0
    errors: list[str] = field(default_factory=list)


async def run_quick_reflection(
    *,
    user_id: int,
    thread_id: int | None = None,
    conversation_text: str = "",
    db_factory: Callable[..., object] | None = None,
) -> QuickReflectionResult:
    """Run a quick post-conversation reflection."""
    result = QuickReflectionResult()

    if settings.agent_provider == "scaffold":
        return result

    from anima_server.db.session import SessionLocal

    factory = db_factory or SessionLocal

    try:
        with factory() as db:
            from anima_server.services.agent.self_model import (
                get_self_model_block,
                set_self_model_block,
                ensure_self_model_exists,
            )

            ensure_self_model_exists(db, user_id=user_id)

            inner_state_block = get_self_model_block(
                db, user_id=user_id, section="inner_state")
            working_memory_block = get_self_model_block(
                db, user_id=user_id, section="working_memory")

            # Get recent episodes
            from sqlalchemy import select
            episodes = db.scalars(
                select(MemoryEpisode)
                .where(MemoryEpisode.user_id == user_id)
                .order_by(MemoryEpisode.created_at.desc())
                .limit(3)
            ).all()
            episodes_text = "\n".join(
                f"- {ep.date}: {df(user_id, ep.summary)}" for ep in reversed(episodes)
            ) or "No recent episodes."

            # If no conversation text, try to get from recent messages
            if not conversation_text:
                conversation_text = await _get_recent_conversation(db, user_id=user_id, thread_id=thread_id)

            if not conversation_text.strip():
                return result

            prompt = QUICK_REFLECTION_PROMPT.format(
                inner_state=df(user_id, inner_state_block.content) if inner_state_block else "No state yet.",
                working_memory=df(user_id, working_memory_block.content) if working_memory_block else "Empty.",
                recent_episodes=episodes_text,
                conversation=conversation_text[:3000],
            )

            response = await _call_llm(prompt, system="You are SAM's inner reflection. Respond only with JSON.")
            if not response:
                return result

            parsed = _parse_json(response)
            if not parsed:
                result.errors.append("Failed to parse reflection response")
                return result

            # Update inner state
            inner_state_data = parsed.get("inner_state")
            if inner_state_data and isinstance(inner_state_data, dict):
                new_inner_state = _format_inner_state(inner_state_data)
                set_self_model_block(
                    db, user_id=user_id, section="inner_state",
                    content=new_inner_state, updated_by="post_turn",
                )
                result.inner_state_updated = True

            # Update working memory
            wm_updates = parsed.get("working_memory_updates", [])
            if wm_updates and isinstance(wm_updates, list):
                current_wm = df(user_id, working_memory_block.content) if working_memory_block else "# Things I'm Holding in Mind\n"
                for update in wm_updates:
                    if not isinstance(update, dict):
                        continue
                    action = update.get("action", "")
                    item = update.get("item", "")
                    if not item:
                        continue
                    expires = update.get("expires")
                    if action == "add":
                        entry = f"- {item}"
                        if expires:
                            entry += f" [expires: {expires}]"
                        current_wm += f"\n{entry}"
                    elif action == "remove":
                        current_wm = current_wm.replace(f"- {item}", "")
                set_self_model_block(
                    db, user_id=user_id, section="working_memory",
                    content=current_wm.strip(), updated_by="post_turn",
                )
                result.working_memory_updated = True

            # Record emotional signal
            emotional = parsed.get("emotional_read", {})
            if isinstance(emotional, dict) and emotional.get("emotion"):
                from anima_server.services.agent.emotional_intelligence import record_emotional_signal
                signal = record_emotional_signal(
                    db,
                    user_id=user_id,
                    thread_id=thread_id,
                    emotion=emotional["emotion"],
                    confidence=float(emotional.get("confidence", 0.5)),
                    evidence_type="linguistic",
                    evidence=str(emotional.get("evidence", "")),
                    trajectory=str(emotional.get("trajectory", "stable")),
                )
                result.emotional_signal_recorded = signal is not None

            result.quick_take = parsed.get("quick_take", "")
            db.commit()

    except Exception as e:
        logger.exception("Quick reflection failed for user %s", user_id)
        result.errors.append(str(e))

    return result


async def run_deep_monologue(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> DeepMonologueResult:
    """Run a deep reflection cycle, updating all self-model sections."""
    result = DeepMonologueResult()

    if settings.agent_provider == "scaffold":
        return result

    from anima_server.db.session import SessionLocal

    factory = db_factory or SessionLocal

    try:
        with factory() as db:
            from anima_server.services.agent.self_model import (
                get_all_self_model_blocks,
                set_self_model_block,
                append_growth_log_entry,
                ensure_self_model_exists,
            )
            from anima_server.services.agent.memory_store import get_memory_items
            from anima_server.services.agent.emotional_intelligence import get_recent_signals

            ensure_self_model_exists(db, user_id=user_id)
            blocks = get_all_self_model_blocks(db, user_id=user_id)

            # Gather context
            from sqlalchemy import select
            episodes = db.scalars(
                select(MemoryEpisode)
                .where(MemoryEpisode.user_id == user_id)
                .order_by(MemoryEpisode.created_at.desc())
                .limit(10)
            ).all()
            episodes_text = "\n".join(
                f"- {ep.date}: {df(user_id, ep.summary)} (topics: {', '.join(ep.topics_json or [])})"
                for ep in reversed(episodes)
            ) or "No episodes yet."

            facts = get_memory_items(
                db, user_id=user_id, category="fact", limit=30)
            facts_text = "\n".join(
                f"- {df(user_id, f.content)}" for f in facts) or "No facts yet."

            signals = get_recent_signals(db, user_id=user_id, limit=10)
            signals_text = "\n".join(
                f"- {s.emotion} (confidence: {s.confidence:.1f}, {s.trajectory})"
                + (f" — {df(user_id, s.evidence)[:80]}" if s.evidence else "")
                for s in signals
            ) or "No emotional signals yet."

            identity_block = blocks.get("identity")
            identity_version = identity_block.version if identity_block else 1

            # Load persona block (living style/approach)
            from anima_server.models import SelfModelBlock
            from sqlalchemy import select as sa_select
            persona_block = db.scalar(
                sa_select(SelfModelBlock).where(
                    SelfModelBlock.user_id == user_id,
                    SelfModelBlock.section == "persona",
                )
            )
            persona_text = df(user_id, persona_block.content) if persona_block else "Default persona — not yet customized."

            prompt = DEEP_MONOLOGUE_PROMPT.format(
                identity_version=identity_version,
                identity=df(user_id, identity_block.content) if identity_block else "Not yet created.",
                persona=persona_text[:1000],
                inner_state=df(user_id, blocks["inner_state"].content) if "inner_state" in blocks else "No state.",
                working_memory=df(user_id, blocks["working_memory"].content) if "working_memory" in blocks else "Empty.",
                growth_log=_last_n_entries(
                    df(user_id, blocks["growth_log"].content) if "growth_log" in blocks else "", 5
                ),
                intentions=df(user_id, blocks["intentions"].content) if "intentions" in blocks else "None.",
                user_facts=facts_text[:2000],
                recent_episodes=episodes_text[:2000],
                emotional_signals=signals_text[:1000],
            )

            response = await _call_llm(
                prompt,
                system="You are SAM's deep inner monologue. Think genuinely. Respond only with JSON.",
            )
            if not response:
                return result

            parsed = _parse_json(response)
            if not parsed:
                result.errors.append("Failed to parse monologue response")
                return result

            # Apply updates
            if parsed.get("identity_update"):
                set_self_model_block(
                    db, user_id=user_id, section="identity",
                    content=parsed["identity_update"],
                    updated_by="sleep_time",
                )
                result.identity_updated = True

            if parsed.get("persona_update"):
                # Evolve the persona block — the agent's living style/approach
                if persona_block is not None:
                    persona_block.content = ef(user_id, parsed["persona_update"])
                    persona_block.version += 1
                    persona_block.updated_by = "sleep_time"
                else:
                    db.add(SelfModelBlock(
                        user_id=user_id,
                        section="persona",
                        content=ef(user_id, parsed["persona_update"]),
                        version=1,
                        updated_by="sleep_time",
                    ))
                result.persona_updated = True

            if parsed.get("inner_state_update"):
                set_self_model_block(
                    db, user_id=user_id, section="inner_state",
                    content=parsed["inner_state_update"],
                    updated_by="sleep_time",
                )
                result.inner_state_updated = True

            if parsed.get("working_memory_update"):
                set_self_model_block(
                    db, user_id=user_id, section="working_memory",
                    content=parsed["working_memory_update"],
                    updated_by="sleep_time",
                )
                result.working_memory_updated = True

            if parsed.get("growth_log_entry"):
                append_growth_log_entry(
                    db, user_id=user_id,
                    entry=parsed["growth_log_entry"],
                )
                result.growth_log_entry_added = True

            if parsed.get("intentions_update"):
                set_self_model_block(
                    db, user_id=user_id, section="intentions",
                    content=parsed["intentions_update"],
                    updated_by="sleep_time",
                )
                result.intentions_updated = True

            # Add procedural rules
            rules = parsed.get("new_procedural_rules", [])
            if isinstance(rules, list):
                from anima_server.services.agent.intentions import add_procedural_rule
                for rule_data in rules:
                    if isinstance(rule_data, dict) and rule_data.get("rule"):
                        add_procedural_rule(
                            db, user_id=user_id,
                            rule=rule_data["rule"],
                            evidence=rule_data.get("evidence", ""),
                            confidence=rule_data.get("confidence", "low"),
                        )
                        result.procedural_rules_added += 1

            result.insights_generated = len(parsed.get("insights", []))

            db.commit()

    except Exception as e:
        logger.exception("Deep monologue failed for user %s", user_id)
        result.errors.append(str(e))

    return result


async def _call_llm(prompt: str, system: str = "") -> str:
    """Call the LLM for reflection."""
    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage

        llm = create_llm()
        messages = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=prompt))
        response = await llm.ainvoke(messages)
        content = getattr(response, "content", "")
        return content if isinstance(content, str) else str(content)
    except Exception:
        logger.exception("Inner monologue LLM call failed")
        return ""


async def _get_recent_conversation(
    db: Session,
    *,
    user_id: int,
    thread_id: int | None = None,
) -> str:
    """Get the recent conversation text for reflection."""
    from sqlalchemy import select
    from anima_server.models import AgentMessage, AgentThread

    if thread_id is None:
        thread = db.scalar(
            select(AgentThread).where(AgentThread.user_id == user_id)
        )
        if thread is None:
            return ""
        thread_id = thread.id

    messages = db.scalars(
        select(AgentMessage)
        .where(
            AgentMessage.thread_id == thread_id,
            AgentMessage.is_in_context.is_(True),
            AgentMessage.role.in_(("user", "assistant")),
        )
        .order_by(AgentMessage.sequence_id.desc())
        .limit(20)
    ).all()

    lines = []
    for msg in reversed(messages):
        role = "User" if msg.role == "user" else "SAM"
        text = df(user_id, msg.content_text or "").strip()
        if text:
            lines.append(f"{role}: {text}")

    return "\n".join(lines)


def _format_inner_state(data: dict) -> str:
    """Format inner state data into markdown."""
    lines = ["# Current Sense of the User", ""]
    lines.append(data.get("sense_of_user", "No strong signals."))
    lines.append("")
    lines.append("# Active Threads")
    lines.append("")
    for thread in data.get("active_threads", []):
        lines.append(f"- {thread}")
    if not data.get("active_threads"):
        lines.append("No ongoing threads.")
    lines.append("")
    lines.append("# Things I'm Curious About")
    lines.append("")
    for q in data.get("curious_about", []):
        lines.append(f"- {q}")
    lines.append("")
    lines.append("# Recent Observations")
    lines.append("")
    for obs in data.get("observations", []):
        lines.append(f"- {obs}")
    return "\n".join(lines)


def _last_n_entries(growth_log: str, n: int) -> str:
    """Get the last N entries from a growth log."""
    if not growth_log.strip():
        return "No entries yet."
    entries = [e.strip() for e in growth_log.split("### ") if e.strip()]
    last_entries = entries[-n:] if len(entries) > n else entries
    return "\n\n".join(f"### {e}" for e in last_entries) if last_entries else "No entries yet."


def _parse_json(text: str) -> dict | None:
    """Extract a JSON object from LLM response."""
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
