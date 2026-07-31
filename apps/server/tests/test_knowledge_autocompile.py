from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from anima_server.config import settings
from anima_server.models.runtime import (
    RuntimeKnowledgeBundleRun,
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeConceptSource,
    RuntimeKnowledgeLink,
)
from anima_server.services.agent.sleep_agent import _task_knowledge_autocompile
from anima_server.services.ingestion.adapters.text import (
    ingest_markdown_content,
    ingest_text_content,
)
from anima_server.services.ingestion.document_compiler import (
    compile_source_knowledge_auto,
    compile_source_knowledge_llm,
    find_autocompile_candidates,
)
from sqlalchemy import select

pytest_plugins = ("conftest_runtime",)

USER_ID = 7


class _ScriptedClient:
    """Chat client returning canned payloads, recording prompts."""

    def __init__(self, *payloads: str) -> None:
        self._payloads = list(payloads)
        self.prompts: list[str] = []

    async def ainvoke(self, messages: Any) -> Any:
        self.prompts.append("\n".join(str(m.content) for m in messages))
        return SimpleNamespace(content=self._payloads.pop(0))


class _DownClient:
    async def ainvoke(self, messages: Any) -> Any:
        raise RuntimeError("model unavailable")


def _ingest_markdown(runtime_db, content: str, *, filename: str):
    source, _artifacts, spans = ingest_markdown_content(
        runtime_db,
        user_id=USER_ID,
        content=content,
        filename=filename,
        compile_knowledge=False,
    )
    return source, spans


def _evidence_ids(spans) -> list[int]:
    return [span.id for span in spans if span.span_kind != "section"]


def _llm_payload(concepts: list[dict], links: list[dict] | None = None) -> str:
    return json.dumps(
        {"concepts": concepts, "links": links or [], "metadata": {"compiler": "llm_wiki"}}
    )


@pytest.mark.asyncio()
async def test_llm_compile_produces_cited_concepts(runtime_db) -> None:
    source, spans = _ingest_markdown(
        runtime_db,
        "# Relays\n\nInspect relays before restart.",
        filename="relays.md",
    )
    span_ids = _evidence_ids(spans)
    client = _ScriptedClient(
        _llm_payload(
            [
                {
                    "type": "source_summary",
                    "slug": f"summary-relays-{source.id}",
                    "title": "Relays",
                    "description": "Summary.",
                    "body_markdown": "# Relays\n\nRelay maintenance overview.",
                    "source_span_ids": span_ids,
                    "tags": ["compiled"],
                },
                {
                    "type": "topic",
                    "slug": "relay-inspection",
                    "title": "Relay Inspection",
                    "description": "When to inspect relays.",
                    "body_markdown": "# Relay Inspection\n\nInspect before restart.",
                    "source_span_ids": span_ids[:1],
                    "tags": ["relays"],
                },
            ],
            links=[
                {
                    "source_slug": "relay-inspection",
                    "target_slug": f"summary-relays-{source.id}",
                    "link_type": "supports",
                    "confidence": 0.9,
                }
            ],
        )
    )

    result = await compile_source_knowledge_llm(
        runtime_db, source=source, spans=spans, llm_client=client
    )

    assert result.status == "completed"
    assert result.concept_count == 2
    assert result.link_count == 1
    concept = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(
            RuntimeKnowledgeConcept.slug == "relay-inspection"
        )
    )
    assert concept is not None
    citations = list(
        runtime_db.scalars(
            select(RuntimeKnowledgeConceptSource).where(
                RuntimeKnowledgeConceptSource.concept_id == concept.id
            )
        ).all()
    )
    assert [citation.span_id for citation in citations] == span_ids[:1]
    # Prompt carried source metadata and span ids for citation.
    assert f"span {span_ids[0]}" in client.prompts[0]
    assert "relays.md" in client.prompts[0] or "Relays" in client.prompts[0]


@pytest.mark.asyncio()
async def test_llm_compile_discards_link_types_outside_the_documented_enum(
    runtime_db,
) -> None:
    source, spans = _ingest_markdown(
        runtime_db,
        "# Relays\n\nInspect relays before restart.",
        filename="private-link.md",
    )
    span_ids = _evidence_ids(spans)
    client = _ScriptedClient(
        _llm_payload(
            [
                {
                    "type": "source_summary",
                    "slug": f"summary-relays-{source.id}",
                    "title": "Relays",
                    "description": "Summary.",
                    "body_markdown": "# Relays\n\nRelay maintenance overview.",
                    "source_span_ids": span_ids,
                    "tags": ["compiled"],
                },
                {
                    "type": "topic",
                    "slug": "relay-inspection",
                    "title": "Relay Inspection",
                    "description": "When to inspect relays.",
                    "body_markdown": "# Relay Inspection\n\nInspect before restart.",
                    "source_span_ids": span_ids,
                    "tags": ["relays"],
                },
            ],
            links=[
                {
                    "source_slug": "relay-inspection",
                    "target_slug": f"summary-relays-{source.id}",
                    "link_type": "reveals my private relationship",
                    "confidence": 0.9,
                }
            ],
        )
    )

    result = await compile_source_knowledge_llm(
        runtime_db,
        source=source,
        spans=spans,
        llm_client=client,
    )

    assert result.status == "completed"
    assert result.link_count == 0
    assert runtime_db.scalar(select(RuntimeKnowledgeLink)) is None


@pytest.mark.asyncio()
async def test_llm_compile_merges_same_slug_across_sources(runtime_db) -> None:
    source_a, spans_a = _ingest_markdown(
        runtime_db, "# Relays\n\nRelays from vendor A.", filename="a.md"
    )
    source_b, spans_b = _ingest_markdown(
        runtime_db, "# Relays\n\nRelays from vendor B.", filename="b.md"
    )

    def payload_for(span_ids: list[int]) -> str:
        return _llm_payload(
            [
                {
                    "type": "topic",
                    "slug": "relay-maintenance",
                    "title": "Relay Maintenance",
                    "description": "Merged topic.",
                    "body_markdown": "# Relay Maintenance\n\nMerged evidence.",
                    "source_span_ids": span_ids,
                    "tags": [],
                }
            ]
        )

    first = await compile_source_knowledge_llm(
        runtime_db,
        source=source_a,
        spans=spans_a,
        llm_client=_ScriptedClient(payload_for(_evidence_ids(spans_a))),
    )
    second = await compile_source_knowledge_llm(
        runtime_db,
        source=source_b,
        spans=spans_b,
        llm_client=_ScriptedClient(payload_for(_evidence_ids(spans_b))),
    )

    assert first.status == "completed"
    assert second.status == "completed"
    concepts = list(
        runtime_db.scalars(
            select(RuntimeKnowledgeConcept).where(
                RuntimeKnowledgeConcept.slug == "relay-maintenance"
            )
        ).all()
    )
    assert len(concepts) == 1
    citation_source_ids = set(
        runtime_db.scalars(
            select(RuntimeKnowledgeConceptSource.source_id).where(
                RuntimeKnowledgeConceptSource.concept_id == concepts[0].id
            )
        ).all()
    )
    assert citation_source_ids == {source_a.id, source_b.id}


@pytest.mark.asyncio()
async def test_llm_compile_drops_uncited_concepts(runtime_db) -> None:
    source, spans = _ingest_markdown(
        runtime_db, "# Facts\n\nCited fact body.", filename="facts.md"
    )
    span_ids = _evidence_ids(spans)
    client = _ScriptedClient(
        _llm_payload(
            [
                {
                    "type": "topic",
                    "slug": "cited-topic",
                    "title": "Cited Topic",
                    "description": "ok",
                    "body_markdown": "# Cited",
                    "source_span_ids": span_ids,
                    "tags": [],
                },
                {
                    "type": "topic",
                    "slug": "uncited-topic",
                    "title": "Uncited Topic",
                    "description": "hallucinated",
                    "body_markdown": "# Uncited",
                    "source_span_ids": [],
                    "tags": [],
                },
            ],
            links=[
                {
                    "source_slug": "uncited-topic",
                    "target_slug": "cited-topic",
                    "link_type": "supports",
                }
            ],
        )
    )

    result = await compile_source_knowledge_llm(
        runtime_db, source=source, spans=spans, llm_client=client
    )

    assert result.status == "completed"
    assert result.concept_count == 1
    assert result.link_count == 0
    slugs = set(
        runtime_db.scalars(select(RuntimeKnowledgeConcept.slug)).all()
    )
    assert "cited-topic" in slugs
    assert "uncited-topic" not in slugs


@pytest.mark.asyncio()
async def test_llm_compile_drops_malformed_optional_links(runtime_db) -> None:
    source, spans = _ingest_markdown(
        runtime_db, "# Facts\n\nCited fact body.", filename="malformed-links.md"
    )
    span_ids = _evidence_ids(spans)
    client = _ScriptedClient(
        _llm_payload(
            [
                {
                    "type": "topic",
                    "slug": "cited-topic",
                    "title": "Cited Topic",
                    "description": "ok",
                    "body_markdown": "# Cited",
                    "source_span_ids": span_ids,
                    "tags": [],
                },
                {
                    "type": "topic",
                    "slug": "second-topic",
                    "title": "Second Topic",
                    "description": "ok",
                    "body_markdown": "# Second",
                    "source_span_ids": span_ids,
                    "tags": [],
                },
            ],
            links=[
                {
                    "source_slug": "cited-topic",
                    "target_slug": "second-topic",
                    "link_type": "supports",
                },
                {"source_slug": "cited-topic", "link_type": "supports"},
                {"source_slug": "cited-topic", "target_slug": "second-topic"},
                "not an object",
            ],
        )
    )

    result = await compile_source_knowledge_llm(
        runtime_db, source=source, spans=spans, llm_client=client
    )

    assert result.status == "completed"
    assert result.link_count == 1


@pytest.mark.asyncio()
async def test_llm_compile_fails_run_when_nothing_is_cited(runtime_db) -> None:
    source, spans = _ingest_markdown(
        runtime_db, "# Facts\n\nBody.", filename="allbad.md"
    )
    client = _ScriptedClient(
        _llm_payload(
            [
                {
                    "type": "topic",
                    "slug": "uncited",
                    "title": "Uncited",
                    "description": "x",
                    "body_markdown": "# X",
                    "source_span_ids": [999_999],
                    "tags": [],
                }
            ]
        )
    )

    result = await compile_source_knowledge_llm(
        runtime_db, source=source, spans=spans, llm_client=client
    )

    assert result.status == "failed"
    assert runtime_db.scalar(select(RuntimeKnowledgeConcept)) is None
    run = runtime_db.get(RuntimeKnowledgeBundleRun, result.run_id)
    assert run is not None
    assert run.status == "failed"


@pytest.mark.asyncio()
async def test_llm_compile_malformed_output_leaves_concepts_untouched(
    runtime_db,
) -> None:
    source, spans = _ingest_markdown(
        runtime_db, "# Facts\n\nBody.", filename="malformed.md"
    )

    result = await compile_source_knowledge_llm(
        runtime_db,
        source=source,
        spans=spans,
        llm_client=_ScriptedClient("this is not json at all"),
    )

    assert result.status == "failed"
    assert runtime_db.scalar(select(RuntimeKnowledgeConcept)) is None


@pytest.mark.asyncio()
async def test_llm_compile_falls_back_to_deterministic_when_model_down(
    runtime_db,
) -> None:
    source, spans = _ingest_markdown(
        runtime_db, "# Facts\n\nBody.", filename="down.md"
    )

    result = await compile_source_knowledge_llm(
        runtime_db, source=source, spans=spans, llm_client=_DownClient()
    )

    assert result.status == "completed"
    assert result.concept_count >= 1
    summary = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(
            RuntimeKnowledgeConcept.concept_type == "source_summary"
        )
    )
    assert summary is not None


@pytest.mark.asyncio()
async def test_auto_dispatcher_honors_compiler_setting(
    runtime_db, monkeypatch: Any
) -> None:
    source, spans = _ingest_markdown(
        runtime_db, "# Facts\n\nBody.", filename="dispatch.md"
    )

    monkeypatch.setattr(settings, "knowledge_compiler", "deterministic")
    result = await compile_source_knowledge_auto(
        runtime_db,
        source=source,
        spans=spans,
        llm_client=_ScriptedClient("never used"),
    )
    assert result.status == "completed"

    monkeypatch.setattr(settings, "knowledge_compiler", "llm")
    span_ids = _evidence_ids(spans)
    client = _ScriptedClient(
        _llm_payload(
            [
                {
                    "type": "topic",
                    "slug": "dispatched-topic",
                    "title": "Dispatched",
                    "description": "x",
                    "body_markdown": "# Dispatched",
                    "source_span_ids": span_ids,
                    "tags": [],
                }
            ]
        )
    )
    result = await compile_source_knowledge_auto(
        runtime_db, source=source, spans=spans, llm_client=client
    )
    assert result.status == "completed"
    assert client.prompts  # the LLM path actually consulted the client


def test_find_autocompile_candidates_policy_budget_and_cooldown(runtime_db) -> None:
    markdown_a, _ = _ingest_markdown(runtime_db, "# A\n\nBody A.", filename="a.md")
    markdown_b, _ = _ingest_markdown(runtime_db, "# B\n\nBody B.", filename="b.md")
    markdown_c, _ = _ingest_markdown(runtime_db, "# C\n\nBody C.", filename="c.md")
    text_source, _, _ = ingest_text_content(
        runtime_db,
        user_id=USER_ID,
        content="Plain text body.",
        filename="plain.txt",
        compile_knowledge=False,
    )

    assert (
        find_autocompile_candidates(
            runtime_db, user_id=USER_ID, policy="off", budget=5, cooldown_hours=24
        )
        == []
    )

    markdown_only = find_autocompile_candidates(
        runtime_db, user_id=USER_ID, policy="markdown_only", budget=5, cooldown_hours=24
    )
    assert [source.id for source in markdown_only] == [
        markdown_a.id,
        markdown_b.id,
        markdown_c.id,
    ]

    everything = find_autocompile_candidates(
        runtime_db, user_id=USER_ID, policy="all", budget=5, cooldown_hours=24
    )
    assert text_source.id in {source.id for source in everything}

    budgeted = find_autocompile_candidates(
        runtime_db, user_id=USER_ID, policy="markdown_only", budget=2, cooldown_hours=24
    )
    assert [source.id for source in budgeted] == [markdown_a.id, markdown_b.id]


@pytest.mark.asyncio()
async def test_autocompile_task_compiles_within_budget_and_cooldown(
    runtime_db, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "knowledge_autocompile", "markdown_only")
    monkeypatch.setattr(settings, "knowledge_autocompile_budget_per_cycle", 2)
    for index in range(3):
        _ingest_markdown(
            runtime_db, f"# Doc {index}\n\nBody {index}.", filename=f"doc{index}.md"
        )

    @contextmanager
    def _factory():
        yield runtime_db

    first = await _task_knowledge_autocompile(
        user_id=USER_ID, runtime_db_factory=_factory
    )
    assert first["policy"] == "markdown_only"
    assert len(first["compiled"]) == 2
    assert all(entry["status"] == "completed" for entry in first["compiled"])

    second = await _task_knowledge_autocompile(
        user_id=USER_ID, runtime_db_factory=_factory
    )
    # The third source compiles; the first two are inside the cooldown window
    # and already have concepts.
    assert len(second["compiled"]) == 1

    third = await _task_knowledge_autocompile(
        user_id=USER_ID, runtime_db_factory=_factory
    )
    assert third["compiled"] == []


@pytest.mark.asyncio()
async def test_autocompile_task_off_policy_is_inert(
    runtime_db, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "knowledge_autocompile", "off")
    _ingest_markdown(runtime_db, "# Doc\n\nBody.", filename="inert.md")

    @contextmanager
    def _factory():
        yield runtime_db

    result = await _task_knowledge_autocompile(
        user_id=USER_ID, runtime_db_factory=_factory
    )

    assert result == {"policy": "off", "compiled": []}
    assert runtime_db.scalar(select(RuntimeKnowledgeConcept)) is None


@pytest.mark.asyncio()
async def test_llm_compile_batches_prompts_so_every_span_is_visible(
    runtime_db,
) -> None:
    paragraphs = "\n\n".join(f"Fact number {index}." for index in range(90))
    source, spans = _ingest_markdown(runtime_db, paragraphs, filename="long.md")
    span_ids = _evidence_ids(spans)
    assert len(span_ids) > 80  # forces two prompt batches

    class _BatchClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def ainvoke(self, messages: Any) -> Any:
            prompt = "\n".join(str(m.content) for m in messages)
            self.prompts.append(prompt)
            import re

            visible = [int(m) for m in re.findall(r"- span (\d+)", prompt)]
            batch_number = len(self.prompts)
            from types import SimpleNamespace

            return SimpleNamespace(
                content=_llm_payload(
                    [
                        {
                            "type": "topic",
                            "slug": f"batch-topic-{batch_number}",
                            "title": f"Batch {batch_number}",
                            "description": "x",
                            "body_markdown": f"# Batch {batch_number}",
                            "source_span_ids": visible[:1],
                            "tags": [],
                        }
                    ]
                )
            )

    client = _BatchClient()
    result = await compile_source_knowledge_llm(
        runtime_db, source=source, spans=spans, llm_client=client
    )

    assert result.status == "completed"
    assert len(client.prompts) == 2
    # Every evidence span id appeared in some prompt.
    import re

    seen: set[int] = set()
    for prompt in client.prompts:
        seen.update(int(m) for m in re.findall(r"- span (\d+)", prompt))
    assert seen == set(span_ids)
    # Concepts from both batches persisted.
    slugs = set(runtime_db.scalars(select(RuntimeKnowledgeConcept.slug)).all())
    assert {"batch-topic-1", "batch-topic-2"} <= slugs


@pytest.mark.asyncio()
async def test_llm_compile_coalesces_duplicate_slugs_across_batches(
    runtime_db,
) -> None:
    paragraphs = "\n\n".join(f"Fact number {index}." for index in range(90))
    source, spans = _ingest_markdown(runtime_db, paragraphs, filename="dupes.md")
    span_ids = _evidence_ids(spans)
    assert len(span_ids) > 80

    class _SameSlugClient:
        def __init__(self) -> None:
            self.cited: list[int] = []

        async def ainvoke(self, messages: Any) -> Any:
            import re
            from types import SimpleNamespace

            prompt = "\n".join(str(m.content) for m in messages)
            visible = [int(m) for m in re.findall(r"- span (\d+)", prompt)]
            self.cited.append(visible[0])
            return SimpleNamespace(
                content=_llm_payload(
                    [
                        {
                            "type": "topic",
                            "slug": "shared-topic",
                            "title": "Shared Topic",
                            "description": "x",
                            "body_markdown": "# Shared",
                            "source_span_ids": visible[:1],
                            "tags": [f"batch-{len(self.cited)}"],
                        }
                    ]
                )
            )

    client = _SameSlugClient()
    result = await compile_source_knowledge_llm(
        runtime_db, source=source, spans=spans, llm_client=client
    )

    assert result.status == "completed"
    assert len(client.cited) == 2
    concepts = list(
        runtime_db.scalars(
            select(RuntimeKnowledgeConcept).where(
                RuntimeKnowledgeConcept.slug == "shared-topic"
            )
        ).all()
    )
    assert len(concepts) == 1
    # Citations union across batches instead of the last batch overwriting.
    citation_span_ids = set(
        runtime_db.scalars(
            select(RuntimeKnowledgeConceptSource.span_id).where(
                RuntimeKnowledgeConceptSource.concept_id == concepts[0].id
            )
        ).all()
    )
    assert citation_span_ids == set(client.cited)
    assert set(concepts[0].frontmatter_json["tags"]) >= {"batch-1", "batch-2"}


@pytest.mark.asyncio()
async def test_llm_compile_bounds_long_slugs_and_titles(runtime_db) -> None:
    source, spans = _ingest_markdown(
        runtime_db, "# Facts\n\nBody for bounding.", filename="bounds.md"
    )
    span_ids = _evidence_ids(spans)
    long_slug = "deep-heading-" + "x" * 400
    long_title = "Deep Heading " + "y" * 600
    client = _ScriptedClient(
        _llm_payload(
            [
                {
                    "type": "topic",
                    "slug": long_slug,
                    "title": long_title,
                    "description": "x",
                    "body_markdown": "# Deep",
                    "source_span_ids": span_ids,
                    "tags": [],
                },
                {
                    "type": "source_summary",
                    "slug": "bounds-summary",
                    "title": "Bounds",
                    "description": "s",
                    "body_markdown": "# Bounds",
                    "source_span_ids": span_ids,
                    "tags": [],
                },
            ],
            links=[
                {
                    "source_slug": long_slug,
                    "target_slug": "bounds-summary",
                    "link_type": "supports",
                }
            ],
        )
    )

    result = await compile_source_knowledge_llm(
        runtime_db, source=source, spans=spans, llm_client=client
    )

    assert result.status == "completed"
    assert result.concept_count == 2
    # The link still binds through the bounded slug reference.
    assert result.link_count == 1
    concepts = list(runtime_db.scalars(select(RuntimeKnowledgeConcept)).all())
    assert all(len(concept.slug) <= 255 for concept in concepts)
    assert all(len(concept.title) <= 512 for concept in concepts)


@pytest.mark.asyncio()
async def test_compile_prompt_includes_pdf_section_titles(runtime_db) -> None:
    import hashlib

    from anima_server.models.runtime import (
        RuntimeSource,
        RuntimeSourceArtifact,
        RuntimeSourceSpan,
    )

    def _sha(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    source = RuntimeSource(
        user_id=USER_ID,
        kind="document",
        source_uri="runtime-document://1",
        content_hash=_sha("pdf source"),
        title="manual.pdf",
        media_type="application/pdf",
        status="indexed",
    )
    runtime_db.add(source)
    runtime_db.flush()
    artifact = RuntimeSourceArtifact(
        user_id=USER_ID,
        source_id=source.id,
        artifact_kind="document_text",
        content_text="Relay body.",
        content_hash=_sha("artifact"),
    )
    runtime_db.add(artifact)
    runtime_db.flush()
    span = RuntimeSourceSpan(
        user_id=USER_ID,
        source_id=source.id,
        artifact_id=artifact.id,
        span_kind="document_chunk",
        locator_json={"chunk_index": 0},
        locator_hash=RuntimeSourceSpan.compute_locator_hash({"chunk_index": 0}),
        content_text="Relay body.",
        content_hash=_sha("span"),
        metadata_json={
            "section_title": "Guide (truncated)",
            "source_metadata": {
                "section_paths": ["Guide > Relay Installation Procedures"]
            },
        },
    )
    runtime_db.add(span)
    runtime_db.flush()

    client = _ScriptedClient(
        _llm_payload(
            [
                {
                    "type": "topic",
                    "slug": "relay-topic",
                    "title": "Relay",
                    "description": "x",
                    "body_markdown": "# Relay",
                    "source_span_ids": [span.id],
                    "tags": [],
                }
            ]
        )
    )
    result = await compile_source_knowledge_llm(
        runtime_db, source=source, spans=[span], llm_client=client
    )

    assert result.status == "completed"
    # The prompt carries the full merged path, not the truncated column copy.
    assert "Guide > Relay Installation Procedures" in client.prompts[0]


@pytest.mark.asyncio()
async def test_coalesced_batches_merge_bodies_not_only_citations(runtime_db) -> None:
    paragraphs = "\n\n".join(f"Fact number {index}." for index in range(90))
    source, spans = _ingest_markdown(runtime_db, paragraphs, filename="bodies.md")
    assert len(_evidence_ids(spans)) > 80

    class _SameSlugBodyClient:
        def __init__(self) -> None:
            self.batch = 0

        async def ainvoke(self, messages: Any) -> Any:
            import re
            from types import SimpleNamespace

            prompt = "\n".join(str(m.content) for m in messages)
            visible = [int(m) for m in re.findall(r"- span (\d+)", prompt)]
            self.batch += 1
            return SimpleNamespace(
                content=_llm_payload(
                    [
                        {
                            "type": "source_summary",
                            "slug": "bodies-summary",
                            "title": "Bodies",
                            "description": "s",
                            "body_markdown": (
                                f"# Bodies\n\nEvidence from batch {self.batch}."
                            ),
                            "source_span_ids": visible[:1],
                            "tags": [],
                        }
                    ]
                )
            )

    client = _SameSlugBodyClient()
    result = await compile_source_knowledge_llm(
        runtime_db, source=source, spans=spans, llm_client=client
    )

    assert result.status == "completed"
    concept = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(
            RuntimeKnowledgeConcept.slug == "bodies-summary"
        )
    )
    assert concept is not None
    # Both batches' evidence reaches the page body; the repeated heading is
    # not duplicated.
    assert "Evidence from batch 1." in concept.body_markdown
    assert "Evidence from batch 2." in concept.body_markdown
    assert concept.body_markdown.count("# Bodies") == 1
