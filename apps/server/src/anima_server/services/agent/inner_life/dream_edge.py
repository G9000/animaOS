"""IL7 dream cycle — edge (DB reads/writes, the one extraction-model call, effects).

Mirrors the ``affect.py``(pure) / ``store.py``(edge) split: all arithmetic lives
in ``dream.py``; this module gathers material from the soul/runtime stores, makes
the single extraction-model reflection call, and applies the effects (a
``dream_journal`` row, a 25%-strength affect nudge, an η=0.02 reconsolidation
pass on the touched memories, and raising IL3 ``dream_residue`` for a share-worthy
dream). It is invoked once per idle user from the presence tick, isolated exactly
like ``initiative.tick_initiative_for_user``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import DreamJournal, MemoryItem
from anima_server.models.agent_runtime import LatentTrace
from anima_server.models.runtime import RuntimeThread
from anima_server.models.runtime_consciousness import PresenceCatchup
from anima_server.services.agent.heat_scoring import compute_heat_for_item
from anima_server.services.agent.inner_life.affect import apply_turn_deltas
from anima_server.services.agent.inner_life.dream import (
    DEFAULT_DREAM_CONFIG,
    DreamCandidate,
    DreamConfig,
    is_dream_eligible,
    is_share_worthy,
    sample_material,
    scale_affect_delta,
)
from anima_server.services.agent.inner_life.store import get_affect_state, save_affect_state
from anima_server.services.agent.memory_salience import MEMORY_CLASS_IDENTITY
from anima_server.services.agent.reconsolidation import (
    DEFAULT_DREAM_ETA,
    apply_reconsolidation,
    resolve_current_affect_magnitude,
)
from anima_server.services.data_crypto import DOMAIN_MEMORIES, df, ef
from anima_server.services.sessions import get_active_dek

logger = logging.getLogger(__name__)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _create_extraction_client() -> object:
    """Build a chat client on the EXTRACTION model (not the main agent model)
    — the PRD requires dream narration to use the cheaper extraction model.
    Falls back to the primary provider/model when no extraction target is
    configured."""
    from anima_server.services.agent.llm import create_provider_chat_client

    provider = (settings.agent_extraction_provider or settings.agent_provider).strip()
    model = (settings.agent_extraction_model or settings.agent_model).strip()
    return create_provider_chat_client(
        provider=provider,
        model=model,
        timeout=settings.agent_llm_timeout,
        max_tokens=settings.agent_max_tokens,
        temperature=settings.agent_temperature,
    )


def _idle_hours(runtime_db: Session, *, user_id: int, now: datetime) -> float:
    """Hours since the user's most recent thread activity. No activity at all
    (a brand-new or long-dormant user) counts as maximally idle."""
    latest = runtime_db.scalar(
        select(func.max(RuntimeThread.last_message_at)).where(
            RuntimeThread.user_id == user_id
        )
    )
    latest = _as_utc(latest)
    if latest is None:
        return float("inf")
    return max(0.0, (now.astimezone(UTC) - latest).total_seconds() / 3600.0)


def _night_window_start(local_now: datetime, config: DreamConfig) -> datetime:
    """The start instant of the night window that ``local_now`` falls in (or
    the most recent one), used to enforce the per-night cap. For the default
    00:00–06:00 window this is local midnight of the current day."""
    start = local_now.replace(
        hour=config.night_start_hour, minute=0, second=0, microsecond=0
    )
    if local_now.hour < config.night_start_hour:
        start = start - timedelta(days=1)
    return start


def _dreams_this_night(
    soul_db: Session, *, user_id: int, local_now: datetime, config: DreamConfig
) -> int:
    """Dreams already recorded in the current night window (the per-night cap
    counts from ``dreamt_at``)."""
    window_start = _night_window_start(local_now, config).astimezone(UTC)
    return int(
        soul_db.scalar(
            select(func.count())
            .select_from(DreamJournal)
            .where(
                DreamJournal.user_id == user_id,
                DreamJournal.dreamt_at >= window_start,
            )
        )
        or 0
    )


def _consume_catchup_marker(runtime_db: Session, *, user_id: int) -> bool:
    """Whether a deferred catch-up dream is pending (IL2 set it when an offline
    gap covered an eligible night window). Clears the marker as it reads it, so
    a gap yields exactly ONE wake-up dream regardless of length."""
    row = runtime_db.scalar(
        select(PresenceCatchup).where(
            PresenceCatchup.user_id == user_id,
            PresenceCatchup.dream_deferred.is_(True),
        )
    )
    if row is None:
        return False
    row.dream_deferred = False
    return True


def _gather_candidates(
    soul_db: Session, *, user_id: int, now: datetime, config: DreamConfig
) -> tuple[list[MemoryItem], list[DreamCandidate]]:
    """Active, non-identity memory items as dream candidates. Identity-class
    content is never dream material (PRD: dreams never touch identity-class
    memories). Returns the ORM rows alongside their reduced candidates so the
    caller can map sampled refs back for content + reconsolidation."""
    rows = list(
        soul_db.scalars(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.superseded_by.is_(None),
                MemoryItem.distilled_at.is_(None),
                MemoryItem.memory_class != MEMORY_CLASS_IDENTITY,
            )
        ).all()
    )
    candidates = [
        DreamCandidate(
            ref=row.id,
            importance=int(row.importance or 0),
            emotional_salience=float(row.emotional_salience or 0.0),
            heat=compute_heat_for_item(row, now=now),
        )
        for row in rows
    ]
    return rows, candidates


def _random_transcript_fragment(
    user_id: int, dek: bytes, rng: random.Random
) -> str | None:
    """One random old user-utterance fragment from the on-disk transcript
    archive. Best-effort: any I/O or decryption failure yields None (the dream
    still runs on memory + latent material)."""
    try:
        from anima_server.services.agent.transcript_archive import (
            decrypt_transcript,
            load_transcript_sidecar,
        )

        transcripts_dir = settings.data_dir / "transcripts"
        if not transcripts_dir.exists():
            return None
        metas = list(transcripts_dir.glob("*.meta.json"))
        rng.shuffle(metas)
        for meta_path in metas:
            sidecar = load_transcript_sidecar(meta_path)
            if not sidecar or sidecar.get("user_id") != user_id:
                continue
            thread_id = sidecar.get("thread_id")
            enc_path = meta_path.with_name(meta_path.name.replace(".meta.json", ""))
            if not enc_path.exists() or thread_id is None:
                continue
            messages = decrypt_transcript(enc_path, dek=dek, thread_id=int(thread_id))
            user_msgs = [
                str(m.get("content", "")).strip()
                for m in messages
                if m.get("role") == "user" and str(m.get("content", "")).strip()
            ]
            if user_msgs:
                fragment = rng.choice(user_msgs)
                return fragment[:280]
        return None
    except Exception:
        logger.debug("Transcript fragment unavailable for dream (user %s)", user_id, exc_info=True)
        return None


async def generate_dream_narrative(
    soul_db: Session,
    *,
    user_id: int,
    material: list[str],
    latent_topics: list[str],
    transcript_fragment: str | None,
    affect_line: str,
    client: object | None = None,
) -> dict[str, object] | None:
    """The ONE extraction-model reflection call. Returns a dict with
    ``narrative`` (str) and ``valence_delta``/``arousal_delta``/``energy_delta``
    (floats, pre-scaling), or None on failure/empty output."""
    from anima_server.services.agent.llm_json import call_llm_for_json
    from anima_server.services.agent.prompt_loader import PromptLoader

    try:
        prompt = PromptLoader.from_db(soul_db, user_id).dream_narrative(
            material=material,
            latent_topics=latent_topics,
            transcript_fragment=transcript_fragment,
            affect_line=affect_line,
        )
        result = await call_llm_for_json(
            "You are the companion's dreaming mind. Recombine the fragments into "
            "ONE short, strange, first-person dream — impressionistic, not a "
            "summary. Never invent facts about the user; only recombine what is "
            "given. Respond as JSON.",
            prompt,
            expect="object",
            client=client or _create_extraction_client(),
        )
    except Exception:
        logger.warning("Dream generation raised for user %s", user_id, exc_info=True)
        return None

    if not isinstance(result, dict):
        return None
    narrative = str(result.get("narrative", "")).strip()
    if not narrative:
        return None
    return {
        "narrative": narrative,
        "valence_delta": float(result.get("valence_delta", 0.0) or 0.0),
        "arousal_delta": float(result.get("arousal_delta", 0.0) or 0.0),
        "energy_delta": float(result.get("energy_delta", 0.0) or 0.0),
    }


def _prune_journal(soul_db: Session, *, user_id: int, cap: int) -> None:
    """Enforce the rolling per-user cap: delete oldest rows beyond ``cap``."""
    ids = list(
        soul_db.scalars(
            select(DreamJournal.id)
            .where(DreamJournal.user_id == user_id)
            .order_by(DreamJournal.dreamt_at.desc())
            .offset(cap)
        ).all()
    )
    for row in soul_db.scalars(select(DreamJournal).where(DreamJournal.id.in_(ids))).all():
        soul_db.delete(row)


def run_dream_for_user(
    soul_db_factory: Callable[..., Session],
    runtime_db_factory: Callable[..., Session],
    *,
    user_id: int,
    local_now: datetime,
    config: DreamConfig = DEFAULT_DREAM_CONFIG,
    rng: random.Random | None = None,
    client: object | None = None,
) -> bool:
    """Run one dream for an idle user if eligible. Returns whether a dream was
    recorded. Isolated exactly like ``tick_initiative_for_user``: its own
    session pair, all exceptions logged and swallowed so one user never aborts
    the sweep. No effect ever happens without an active memories DEK (df/ef
    fail open — a dream without a DEK would feed ciphertext to the LLM and store
    an unencrypted narrative, so it is skipped, mirroring IL3)."""
    rng = rng or random.Random()
    try:
        with runtime_db_factory() as runtime_db, soul_db_factory() as soul_db:
            catchup = _consume_catchup_marker(runtime_db, user_id=user_id)
            idle_hours = _idle_hours(runtime_db, user_id=user_id, now=local_now)
            dreams_tonight = _dreams_this_night(
                soul_db, user_id=user_id, local_now=local_now, config=config
            )
            # Two eligibility paths: the normal night-window gate, OR a deferred
            # catch-up dream (still idle + under the per-night cap, but the night
            # window is waived — it is the "while you were away" wake-up dream).
            eligible = is_dream_eligible(
                idle_hours=idle_hours,
                local_hour=local_now.hour,
                dreams_tonight=dreams_tonight,
                config=config,
            ) or (
                catchup
                and idle_hours >= config.idle_hours_min
                and dreams_tonight < config.max_dreams_per_night
            )
            if not eligible:
                if catchup:
                    runtime_db.commit()  # persist the cleared marker
                return False

            # df/ef fail open without a DEK — never dream on a locked session.
            dek = get_active_dek(user_id, DOMAIN_MEMORIES)
            if dek is None:
                if catchup:
                    runtime_db.commit()
                return False

            rows, candidates = _gather_candidates(
                soul_db, user_id=user_id, now=local_now, config=config
            )
            if not candidates:
                if catchup:
                    runtime_db.commit()
                return False
            selected = sample_material(candidates, rng, config)
            selected_refs = {c.ref for c in selected}
            selected_rows = [r for r in rows if r.id in selected_refs]
            material = [
                df(user_id, r.content, table="memory_items", field="content")
                for r in selected_rows
            ]
            latent_topics = list(
                soul_db.scalars(
                    select(LatentTrace.topic_key).where(
                        LatentTrace.user_id == user_id,
                        LatentTrace.weight >= config.latent_weight_min,
                    )
                ).all()
            )
            transcript_fragment = _random_transcript_fragment(user_id, dek, rng)
            affect_line = _resolve_affect_line(runtime_db, user_id=user_id, now=local_now)

            generated = asyncio.run(
                generate_dream_narrative(
                    soul_db,
                    user_id=user_id,
                    material=material,
                    latent_topics=latent_topics,
                    transcript_fragment=transcript_fragment,
                    affect_line=affect_line,
                    client=client,
                )
            )
            if generated is None:
                if catchup:
                    runtime_db.commit()
                return False

            share_worthy = is_share_worthy(selected, config)
            v, a, e = scale_affect_delta(
                float(generated["valence_delta"]),
                float(generated["arousal_delta"]),
                float(generated["energy_delta"]),
                config,
            )

            # Effect 1: dream_journal row (narrative field-encrypted; refs/deltas
            # are numeric/structural provenance only). Then enforce the cap.
            soul_db.add(
                DreamJournal(
                    user_id=user_id,
                    dreamt_at=local_now.astimezone(UTC),
                    narrative=ef(user_id, generated["narrative"], table="dream_journal", field="narrative"),
                    source_refs={
                        "memory_item_ids": sorted(selected_refs),
                        "latent_topic_keys": latent_topics,
                        "transcript_fragment_used": transcript_fragment is not None,
                    },
                    affect_delta={"valence": v, "arousal": a, "energy": e},
                    share_worthy=share_worthy,
                    surfaced=False,
                )
            )
            soul_db.flush()
            _prune_journal(soul_db, user_id=user_id, cap=config.journal_cap)

            # Effect 2: 25%-strength affect nudge.
            affect_state = get_affect_state(runtime_db, user_id=user_id)
            save_affect_state(
                runtime_db,
                user_id=user_id,
                state=apply_turn_deltas(affect_state, v, a, e),
            )

            # Effect 3: reduced-strength (η=0.02) reconsolidation on touched
            # memories. apply_reconsolidation itself skips superseded/distilled
            # and confines identity to confidence-only.
            magnitude = resolve_current_affect_magnitude(runtime_db, user_id=user_id)
            for row in selected_rows:
                apply_reconsolidation(
                    soul_db, row, current_affect_magnitude=magnitude, eta=DEFAULT_DREAM_ETA
                )

            # Effect 4 (dream_residue) is passive: IL3's resolve_drive_signals
            # reads share-worthy, unsurfaced dream_journal rows — no write here.
            soul_db.commit()
            runtime_db.commit()
            return True
    except Exception:
        logger.warning("Dream cycle failed for user %s", user_id, exc_info=True)
        return False


def _resolve_affect_line(runtime_db: Session, *, user_id: int, now: datetime) -> str:
    try:
        from anima_server.services.agent.inner_life.affect import relax, render_affect
        from anima_server.services.agent.inner_life.store import get_affect_config

        config = get_affect_config()
        stored = get_affect_state(runtime_db, user_id=user_id, config=config)
        current = relax(stored, now.astimezone(UTC), config)
        return render_affect(current, previous=stored)
    except Exception:
        return "steady"
