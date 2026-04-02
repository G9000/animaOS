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
from anima_server.services.agent.json_utils import parse_json_object as _parse_json
from anima_server.services.data_crypto import df

logger = logging.getLogger(__name__)


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


async def _call_llm(prompt: str, system: str) -> str | None:
    """Call the configured LLM with the given prompt and system message."""
    try:
        from anima_server.services.agent.llm import create_llm
        from anima_server.services.agent.messages import HumanMessage, SystemMessage

        llm = create_llm()
        response = await llm.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=prompt)]
        )
        content = getattr(response, "content", "")
        return content if isinstance(content, str) else str(content)
    except Exception:
        logger.exception("LLM call failed in inner monologue")
        return None


async def run_quick_reflection(
    *,
    user_id: int,
    thread_id: str | None = None,
    conversation_text: str = "",
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> QuickReflectionResult:
    """Run a quick post-conversation reflection.

    This updates inner_state and working_memory. It runs automatically after
    the user has been idle for ~5 minutes following an agent turn.
    """
    result = QuickReflectionResult()

    if settings.agent_provider == "scaffold":
        return result

    try:
        from anima_server.db.session import SessionLocal
        from anima_server.services.agent.emotional_intelligence import (
            record_emotional_signal,
        )
        from anima_server.services.agent.prompt_loader import get_prompt_loader
        from anima_server.services.agent.self_model import (
            get_self_model_block,
            get_working_context,
            render_self_model_section,
            set_self_model_block,
            set_working_context,
        )

        factory = db_factory or SessionLocal
        runtime_factory = _get_runtime_factory(runtime_db_factory)

        with factory() as db:
            # Load prompt loader with agent name
            prompt_loader = get_prompt_loader(db, user_id)

            if runtime_factory is None:
                inner_state_block = get_self_model_block(
                    db, user_id=user_id, section="inner_state")
                working_memory_block = get_self_model_block(
                    db, user_id=user_id, section="working_memory"
                )
            else:
                with runtime_factory() as pg_db:
                    working_context = get_working_context(
                        pg_db, user_id=user_id)
                    inner_state_block = working_context.get("inner_state")
                    working_memory_block = working_context.get(
                        "working_memory")

            inner_state_text = (
                render_self_model_section(
                    inner_state_block, user_id=user_id) or "No state yet."
            )
            working_memory_text = (
                render_self_model_section(
                    working_memory_block, user_id=user_id) or "Empty."
            )

            # Get recent episodes
            from sqlalchemy import select

            episodes = db.scalars(
                select(MemoryEpisode)
                .where(MemoryEpisode.user_id == user_id)
                .order_by(MemoryEpisode.created_at.desc())
                .limit(3)
            ).all()
            episodes_text = (
                "\n".join(
                    f"- {ep.date}: {df(user_id, ep.summary, table='memory_episodes', field='summary')}"
                    for ep in reversed(episodes)
                )
                or "No recent episodes."
            )

            # If no conversation text, try to get from recent messages
            if not conversation_text:
                conversation_text = await _get_recent_conversation(
                    db, user_id=user_id, thread_id=thread_id
                )

            if not conversation_text.strip():
                return result

            # Render prompt using template
            prompt = prompt_loader.quick_reflection(
                inner_state=inner_state_text,
                working_memory=working_memory_text,
                recent_episodes=episodes_text,
                conversation=conversation_text[:2000],
            )

            system_prompt = prompt_loader.quick_reflection_system()

            response = await _call_llm(prompt, system=system_prompt)
            if not response:
                return result

            parsed = _parse_json(response)
            if not parsed:
                result.errors.append("Failed to parse reflection response")
                return result

            # Update inner state
            inner_state_data = parsed.get("inner_state")
            wm_updates = parsed.get("working_memory_updates", [])

            current_wm = (
                working_memory_text
                if working_memory_block is not None
                else "# Things I'm Holding in Mind\n"
            )
            if wm_updates and isinstance(wm_updates, list):
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

            # Record emotional signal
            emotional = parsed.get("emotional_read", {})
            if runtime_factory is None:
                if inner_state_data and isinstance(inner_state_data, dict):
                    new_inner_state = _format_inner_state(inner_state_data)
                    set_self_model_block(
                        db,
                        user_id=user_id,
                        section="inner_state",
                        content=new_inner_state,
                        updated_by="post_turn",
                    )
                    result.inner_state_updated = True

                if wm_updates and isinstance(wm_updates, list):
                    set_self_model_block(
                        db,
                        user_id=user_id,
                        section="working_memory",
                        content=current_wm.strip(),
                        updated_by="post_turn",
                    )
                    result.working_memory_updated = True

                if isinstance(emotional, dict) and emotional.get("emotion"):
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
            else:
                with runtime_factory() as pg_db:
                    if inner_state_data and isinstance(inner_state_data, dict):
                        new_inner_state = _format_inner_state(inner_state_data)
                        set_working_context(
                            pg_db,
                            user_id=user_id,
                            section="inner_state",
                            content=new_inner_state,
                            updated_by="post_turn",
                        )
                        result.inner_state_updated = True

                    if wm_updates and isinstance(wm_updates, list):
                        set_working_context(
                            pg_db,
                            user_id=user_id,
                            section="working_memory",
                            content=current_wm.strip(),
                            updated_by="post_turn",
                        )
                        result.working_memory_updated = True

                    if isinstance(emotional, dict) and emotional.get("emotion"):
                        signal = record_emotional_signal(
                            pg_db,
                            user_id=user_id,
                            thread_id=thread_id,
                            emotion=emotional["emotion"],
                            confidence=float(emotional.get("confidence", 0.5)),
                            evidence_type="linguistic",
                            evidence=str(emotional.get("evidence", "")),
                            trajectory=str(emotional.get(
                                "trajectory", "stable")),
                        )
                        result.emotional_signal_recorded = signal is not None

                    pg_db.commit()

            result.quick_take = parsed.get("quick_take", "")
            db.commit()

    except Exception as e:
        logger.exception("Quick reflection failed for user %s", user_id)
        result.errors.append(str(e))

    return result


async def _run_deep_monologue_legacy(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
) -> DeepMonologueResult:
    """Legacy deep monologue (pre-P3). Kept for reference only."""
    result = DeepMonologueResult()

    if settings.agent_provider == "scaffold":
        return result

    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.prompt_loader import get_prompt_loader
    from anima_server.services.agent.self_model import (
        get_all_self_model_blocks,
    )

    factory = db_factory or SessionLocal

    try:
        # ── Phase 1: Read — gather all context in one session ───
        with factory() as db:
            # Load prompt loader with agent name
            prompt_loader = get_prompt_loader(db, user_id)

            blocks = get_all_self_model_blocks(db, user_id=user_id)

            # Gather user facts
            from anima_server.services.agent.memory_store import get_memory_items

            facts = get_memory_items(db, user_id=user_id, limit=20)
            facts_text = (
                "\n".join(
                    f"- {df(user_id, m.content, table='memory_items', field='content')}"
                    for m in facts
                )
                if facts
                else "No stored facts yet."
            )

            # Gather recent episodes
            from sqlalchemy import select

            episodes = db.scalars(
                select(MemoryEpisode)
                .where(MemoryEpisode.user_id == user_id)
                .order_by(MemoryEpisode.created_at.desc())
                .limit(10)
            ).all()
            episodes_text = (
                "\n\n".join(
                    f"{ep.date}: {df(user_id, ep.summary, table='memory_episodes', field='summary')}"
                    for ep in reversed(episodes)
                )
                or "No episodes yet."
            )

            # Gather emotional signals
            from anima_server.models import EmotionalSignal

            signals = db.scalars(
                select(EmotionalSignal)
                .where(EmotionalSignal.user_id == user_id)
                .order_by(EmotionalSignal.created_at.desc())
                .limit(10)
            ).all()
            signals_text = (
                "\n".join(
                    f"- {s.created_at.date()}: {s.emotion} ({s.trajectory})"
                    + (
                        f" — {df(user_id, s.evidence, table='emotional_signals', field='evidence')[:80]}"
                        if s.evidence
                        else ""
                    )
                    for s in signals
                )
                or "No emotional signals yet."
            )

            identity_block = blocks.get("identity")
            identity_version = identity_block.version if identity_block else 1

            from sqlalchemy import select as sa_select

            from anima_server.models import SelfModelBlock

            persona_block = db.scalar(
                sa_select(SelfModelBlock).where(
                    SelfModelBlock.user_id == user_id,
                    SelfModelBlock.section == "persona",
                )
            )
            persona_text = (
                df(user_id, persona_block.content,
                   table="self_model_blocks", field="content")
                if persona_block
                else "Default persona — not yet customized."
            )
            has_persona_block = persona_block is not None

            # Render prompt using template
            prompt = prompt_loader.deep_monologue(
                identity_version=identity_version,
                identity=df(
                    user_id, identity_block.content, table="self_model_blocks", field="content"
                )
                if identity_block
                else "Not yet created.",
                persona=persona_text[:1000],
                inner_state=df(
                    user_id,
                    blocks["inner_state"].content,
                    table="self_model_blocks",
                    field="content",
                )
                if "inner_state" in blocks
                else "No state.",
                working_memory=df(
                    user_id,
                    blocks["working_memory"].content,
                    table="self_model_blocks",
                    field="content",
                )
                if "working_memory" in blocks
                else "Empty.",
                growth_log=_last_n_entries(
                    df(
                        user_id,
                        blocks["growth_log"].content,
                        table="self_model_blocks",
                        field="content",
                    )
                    if "growth_log" in blocks
                    else "",
                    5,
                ),
                intentions=df(
                    user_id,
                    blocks["intentions"].content,
                    table="self_model_blocks",
                    field="content",
                )
                if "intentions" in blocks
                else "None.",
                user_facts=facts_text[:2000],
                recent_episodes=episodes_text[:2000],
                emotional_signals=signals_text[:1000],
            )

            system_prompt = prompt_loader.deep_monologue_system()

        # Session is now closed — no DB lock held during LLM call.

        # ── Phase 2: LLM call — no session held open ────────────
        response = await _call_llm(prompt, system=system_prompt)
        if not response:
            return result

        parsed = _parse_json(response)
        if not parsed:
            result.errors.append("Failed to parse monologue response")
            return result

        # ── Phase 3: Write — short-lived session for DB updates ──
        with factory() as db:
            from anima_server.services.agent.self_model import (
                append_growth_log_entry,
                set_self_model_block,
            )
            from anima_server.services.agent.soul_blocks import set_soul_block

            if parsed.get("identity_update"):
                set_self_model_block(
                    db,
                    user_id=user_id,
                    section="identity",
                    content=parsed["identity_update"],
                    updated_by="sleep_time",
                )
                result.identity_updated = True

            if parsed.get("persona_update"):
                set_soul_block(
                    db,
                    user_id=user_id,
                    section="persona",
                    content=parsed["persona_update"],
                    updated_by="sleep_time",
                )
                result.persona_updated = True

            if parsed.get("inner_state_update"):
                set_self_model_block(
                    db,
                    user_id=user_id,
                    section="inner_state",
                    content=parsed["inner_state_update"],
                    updated_by="sleep_time",
                )
                result.inner_state_updated = True

            if parsed.get("working_memory_update"):
                set_self_model_block(
                    db,
                    user_id=user_id,
                    section="working_memory",
                    content=parsed["working_memory_update"],
                    updated_by="sleep_time",
                )
                result.working_memory_updated = True

            if parsed.get("growth_log_entry"):
                append_growth_log_entry(
                    db,
                    user_id=user_id,
                    entry=parsed["growth_log_entry"],
                )
                result.growth_log_entry_added = True

            if parsed.get("intentions_update"):
                set_self_model_block(
                    db,
                    user_id=user_id,
                    section="intentions",
                    content=parsed["intentions_update"],
                    updated_by="sleep_time",
                )
                result.intentions_updated = True

            rules = parsed.get("new_procedural_rules", [])
            if rules and isinstance(rules, list):
                # Store rules in intentions block for now
                current_intentions = (
                    df(
                        user_id,
                        blocks["intentions"].content,
                        table="self_model_blocks",
                        field="content",
                    )
                    if "intentions" in blocks
                    else "# Intentions\n"
                )
                rules_text = "\n".join(
                    f"- {r.get('rule', '')} [confidence: {r.get('confidence', 'medium')}]"
                    for r in rules
                    if isinstance(r, dict)
                )
                from anima_server.services.data_crypto import ef

                set_self_model_block(
                    db,
                    user_id=user_id,
                    section="intentions",
                    content=current_intentions + "\n\n## Learned Rules\n" + rules_text,
                    updated_by="sleep_time",
                )
                result.procedural_rules_added = len(rules)

            insights = parsed.get("insights", [])
            if insights and isinstance(insights, list):
                result.insights_generated = len(insights)

            db.commit()

    except Exception as e:
        logger.exception("Deep monologue failed for user %s", user_id)
        result.errors.append(str(e))

    return result


async def run_deep_monologue(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
    runtime_db_factory: Callable[..., object] | None = None,
) -> DeepMonologueResult:
    """Run a deep reflection cycle across soul and runtime stores."""
    result = DeepMonologueResult()

    if settings.agent_provider == "scaffold":
        return result

    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.emotional_intelligence import get_recent_signals
    from anima_server.services.agent.prompt_loader import get_prompt_loader
    from anima_server.services.agent.self_model import (
        append_growth_log_entry,
        get_active_intentions,
        get_growth_log_text,
        get_identity_block,
        get_self_model_block,
        get_working_context,
        render_self_model_section,
        set_active_intentions,
        set_self_model_block,
        set_working_context,
    )

    factory = db_factory or SessionLocal
    runtime_factory = _get_runtime_factory(runtime_db_factory)

    try:
        with factory() as db:
            prompt_loader = get_prompt_loader(db, user_id)

            from sqlalchemy import select

            from anima_server.services.agent.memory_store import get_memory_items

            facts = get_memory_items(db, user_id=user_id, limit=20)
            facts_text = (
                "\n".join(
                    f"- {df(user_id, item.content, table='memory_items', field='content')}"
                    for item in facts
                )
                if facts
                else "No stored facts yet."
            )

            episodes = db.scalars(
                select(MemoryEpisode)
                .where(MemoryEpisode.user_id == user_id)
                .order_by(MemoryEpisode.created_at.desc())
                .limit(10)
            ).all()
            episodes_text = (
                "\n\n".join(
                    f"{episode.date}: "
                    f"{df(user_id, episode.summary, table='memory_episodes', field='summary')}"
                    for episode in reversed(episodes)
                )
                or "No episodes yet."
            )

            identity_block = get_identity_block(db, user_id=user_id)
            identity_version = identity_block.version if identity_block else 1
            identity_text = (
                render_self_model_section(
                    identity_block, user_id=user_id) or "Not yet created."
            )

            persona_block = get_self_model_block(
                db, user_id=user_id, section="persona")
            persona_text = (
                render_self_model_section(persona_block, user_id=user_id)
                if persona_block
                else "Default persona - not yet customized."
            )
            has_persona_block = persona_block is not None
            growth_log_text = _last_n_entries(
                get_growth_log_text(db, user_id=user_id), 5)

            if runtime_factory is None:
                inner_state_block = get_self_model_block(
                    db, user_id=user_id, section="inner_state")
                working_memory_block = get_self_model_block(
                    db,
                    user_id=user_id,
                    section="working_memory",
                )
                intentions_block = get_self_model_block(
                    db, user_id=user_id, section="intentions")

                inner_state_text = (
                    render_self_model_section(
                        inner_state_block, user_id=user_id) or "No state."
                )
                working_memory_text = (
                    render_self_model_section(
                        working_memory_block, user_id=user_id) or "Empty."
                )
                intentions_text = (
                    render_self_model_section(
                        intentions_block, user_id=user_id) or "None."
                )

                from anima_server.models import EmotionalSignal

                signals = db.scalars(
                    select(EmotionalSignal)
                    .where(EmotionalSignal.user_id == user_id)
                    .order_by(EmotionalSignal.created_at.desc())
                    .limit(10)
                ).all()
                signals_text = (
                    "\n".join(
                        f"- {signal.created_at.date()}: {signal.emotion} ({signal.trajectory})"
                        + (
                            f" - {df(user_id, signal.evidence, table='emotional_signals', field='evidence')[:80]}"
                            if signal.evidence
                            else ""
                        )
                        for signal in signals
                    )
                    or "No emotional signals yet."
                )
            else:
                with runtime_factory() as pg_db:
                    working_context = get_working_context(
                        pg_db, user_id=user_id)
                    intentions_block = get_active_intentions(
                        pg_db, user_id=user_id)
                    signals = get_recent_signals(
                        pg_db, user_id=user_id, limit=10)

                    inner_state_text = (
                        render_self_model_section(
                            working_context.get("inner_state"), user_id=user_id)
                        or "No state."
                    )
                    working_memory_text = (
                        render_self_model_section(
                            working_context.get("working_memory"),
                            user_id=user_id,
                        )
                        or "Empty."
                    )
                    intentions_text = (
                        render_self_model_section(
                            intentions_block, user_id=user_id) or "None."
                    )
                    signals_text = (
                        "\n".join(
                            f"- {signal.created_at.date()}: {signal.emotion} ({signal.trajectory})"
                            + (
                                f" - {str(getattr(signal, 'evidence', '') or '')[:80]}"
                                if getattr(signal, "evidence", "")
                                else ""
                            )
                            for signal in signals
                        )
                        or "No emotional signals yet."
                    )

            prompt = prompt_loader.deep_monologue(
                identity_version=identity_version,
                identity=identity_text,
                persona=persona_text[:1000],
                inner_state=inner_state_text,
                working_memory=working_memory_text,
                growth_log=growth_log_text,
                intentions=intentions_text,
                user_facts=facts_text[:2000],
                recent_episodes=episodes_text[:2000],
                emotional_signals=signals_text[:1000],
            )
            system_prompt = prompt_loader.deep_monologue_system()

        response = await _call_llm(prompt, system=system_prompt)
        if not response:
            return result

        parsed = _parse_json(response)
        if not parsed:
            result.errors.append("Failed to parse monologue response")
            return result

        with factory() as db:
            from anima_server.services.agent.soul_blocks import set_soul_block

            if parsed.get("identity_update"):
                set_self_model_block(
                    db,
                    user_id=user_id,
                    section="identity",
                    content=parsed["identity_update"],
                    updated_by="sleep_time",
                )
                result.identity_updated = True

            if parsed.get("persona_update"):
                set_soul_block(
                    db,
                    user_id=user_id,
                    section="persona",
                    content=parsed["persona_update"],
                    updated_by="sleep_time",
                )
                result.persona_updated = True

            if parsed.get("growth_log_entry"):
                append_growth_log_entry(
                    db,
                    user_id=user_id,
                    entry=parsed["growth_log_entry"],
                )
                result.growth_log_entry_added = True

            updated_intentions_text = intentions_text if intentions_text != "None." else "# Intentions\n"

            if runtime_factory is None:
                if parsed.get("inner_state_update"):
                    _inner_val = parsed["inner_state_update"]
                    if not isinstance(_inner_val, str):
                        _inner_val = json.dumps(_inner_val, indent=2)
                    set_self_model_block(
                        db,
                        user_id=user_id,
                        section="inner_state",
                        content=_inner_val,
                        updated_by="sleep_time",
                    )
                    result.inner_state_updated = True

                if parsed.get("working_memory_update"):
                    _wm_val = parsed["working_memory_update"]
                    if not isinstance(_wm_val, str):
                        _wm_val = json.dumps(_wm_val, indent=2)
                    set_self_model_block(
                        db,
                        user_id=user_id,
                        section="working_memory",
                        content=_wm_val,
                        updated_by="sleep_time",
                    )
                    result.working_memory_updated = True

                if parsed.get("intentions_update"):
                    updated_intentions_text = str(parsed["intentions_update"])
                    set_self_model_block(
                        db,
                        user_id=user_id,
                        section="intentions",
                        content=updated_intentions_text,
                        updated_by="sleep_time",
                    )
                    result.intentions_updated = True

                rules = parsed.get("new_procedural_rules", [])
                if rules and isinstance(rules, list):
                    rules_text = "\n".join(
                        f"- {rule.get('rule', '')} [confidence: {rule.get('confidence', 'medium')}]"
                        for rule in rules
                        if isinstance(rule, dict)
                    )
                    set_self_model_block(
                        db,
                        user_id=user_id,
                        section="intentions",
                        content=updated_intentions_text + "\n\n## Learned Rules\n" + rules_text,
                        updated_by="sleep_time",
                    )
                    result.procedural_rules_added = len(rules)
            else:
                with runtime_factory() as pg_db:
                    if parsed.get("inner_state_update"):
                        _inner_val = parsed["inner_state_update"]
                        if not isinstance(_inner_val, str):
                            _inner_val = json.dumps(_inner_val, indent=2)
                        set_working_context(
                            pg_db,
                            user_id=user_id,
                            section="inner_state",
                            content=_inner_val,
                            updated_by="sleep_time",
                        )
                        result.inner_state_updated = True

                    if parsed.get("working_memory_update"):
                        _wm_val = parsed["working_memory_update"]
                        if not isinstance(_wm_val, str):
                            _wm_val = json.dumps(_wm_val, indent=2)
                        set_working_context(
                            pg_db,
                            user_id=user_id,
                            section="working_memory",
                            content=_wm_val,
                            updated_by="sleep_time",
                        )
                        result.working_memory_updated = True

                    if parsed.get("intentions_update"):
                        updated_intentions_text = str(
                            parsed["intentions_update"])
                        set_active_intentions(
                            pg_db,
                            user_id=user_id,
                            content=updated_intentions_text,
                            updated_by="sleep_time",
                        )
                        result.intentions_updated = True

                    rules = parsed.get("new_procedural_rules", [])
                    if rules and isinstance(rules, list):
                        rules_text = "\n".join(
                            f"- {rule.get('rule', '')} [confidence: {rule.get('confidence', 'medium')}]"
                            for rule in rules
                            if isinstance(rule, dict)
                        )
                        set_active_intentions(
                            pg_db,
                            user_id=user_id,
                            content=updated_intentions_text + "\n\n## Learned Rules\n" + rules_text,
                            updated_by="sleep_time",
                        )
                        result.procedural_rules_added = len(rules)

                    pg_db.commit()

            # Promote recurring emotional signals to enduring soul patterns
            if runtime_factory is not None:
                try:
                    from anima_server.services.agent.emotional_patterns import (
                        promote_emotional_patterns,
                    )

                    with runtime_factory() as pg_db:
                        promote_emotional_patterns(
                            soul_db=db, pg_db=pg_db, user_id=user_id
                        )
                except Exception:
                    logger.debug(
                        "Emotional pattern promotion skipped in deep monologue for user %s",
                        user_id,
                    )

            insights = parsed.get("insights", [])
            if insights and isinstance(insights, list):
                result.insights_generated = len(insights)

            db.commit()

    except Exception as e:
        logger.exception("Deep monologue failed for user %s", user_id)
        result.errors.append(str(e))

    return result


async def _get_recent_conversation(
    db: Session, user_id: int, thread_id: str | None, limit: int = 10
) -> str:
    """Fetch recent conversation from RuntimeMessage as formatted text."""
    from anima_server.db.runtime import get_runtime_session_factory

    try:
        rt_factory = get_runtime_session_factory()
    except RuntimeError:
        return ""

    from sqlalchemy import select

    from anima_server.models.runtime import RuntimeMessage

    with rt_factory() as rt_db:
        messages = list(
            rt_db.scalars(
                select(RuntimeMessage)
                .where(
                    RuntimeMessage.user_id == user_id,
                    RuntimeMessage.role.in_(("user", "assistant")),
                )
                .order_by(RuntimeMessage.created_at.desc())
                .limit(limit * 2)  # each pair = 2 messages
            ).all()
        )

    lines = []
    for msg in reversed(messages):
        content = (msg.content_text or "")[:500]
        prefix = "User" if msg.role == "user" else "Assistant"
        lines.append(f"{prefix}: {content}")
    return "\n\n".join(lines)


def _format_inner_state(data: dict) -> str:
    """Format inner state data as markdown."""
    lines = ["# Inner State"]
    if "sense_of_user" in data:
        lines.append(f"\n## Sense of User\n{data['sense_of_user']}")
    if "active_threads" in data:
        lines.append("\n## Active Threads")
        for thread in data["active_threads"]:
            lines.append(f"- {thread}")
    if "curious_about" in data:
        lines.append("\n## Curious About")
        for item in data["curious_about"]:
            lines.append(f"- {item}")
    if "observations" in data:
        lines.append("\n## Observations")
        for obs in data["observations"]:
            lines.append(f"- {obs}")
    return "\n".join(lines)


def _last_n_entries(content: str, n: int) -> str:
    """Extract the last n entries from a markdown list."""
    if not content:
        return "No entries yet."
    entries = [line for line in content.split(
        "\n") if line.strip().startswith(("- ", "* "))]
    return "\n".join(entries[-n:]) if entries else "No entries yet."


def _get_runtime_factory(
    runtime_db_factory: Callable[..., object] | None,
) -> Callable[..., object] | None:
    if runtime_db_factory is not None:
        return runtime_db_factory

    from anima_server.db.runtime import get_runtime_session_factory

    try:
        return get_runtime_session_factory()
    except RuntimeError:
        return None
