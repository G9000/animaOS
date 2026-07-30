from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from anima_server.models.runtime import (
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeConceptSource,
    RuntimeKnowledgeLink,
    RuntimeSource,
    RuntimeSourceArtifact,
    RuntimeSourceSpan,
)
from anima_server.services.ingestion.okf import export_okf_bundle, import_okf_bundle
from sqlalchemy import select

pytest_plugins = ("conftest_runtime",)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _concept(
    *,
    user_id: int = 1,
    concept_type: str = "topic",
    slug: str = "topic-runtime-ingestion",
    title: str = "Runtime ingestion",
    body_markdown: str = "Compiled notes.",
    frontmatter_json: dict[str, object] | None = None,
) -> RuntimeKnowledgeConcept:
    frontmatter = {
        "type": concept_type,
        "title": title,
        "description": "A source-type-agnostic ingestion topic.",
        "resource": "https://example.test/resource",
        "tags": ["ingestion", "okf"],
        "timestamp": "2026-07-06T00:00:00+08:00",
        "x_anima_unknown": {"kept": True},
    }
    if frontmatter_json:
        frontmatter.update(frontmatter_json)
    return RuntimeKnowledgeConcept(
        user_id=user_id,
        concept_type=concept_type,
        slug=slug,
        title=title,
        description=str(frontmatter.get("description", "")),
        body_markdown=body_markdown,
        frontmatter_json=frontmatter,
        metadata_json={"source": "test"},
        content_hash=_sha(body_markdown),
        status="active",
    )


def _read_frontmatter(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, yaml_text, body = text.split("---\n", maxsplit=2)
    return yaml.safe_load(yaml_text), body.lstrip("\n")


def _seed_cited_concept(runtime_db) -> RuntimeKnowledgeConcept:
    concept = _concept(
        slug="topic-citable",
        title="Citable topic",
        body_markdown="Compiled notes with source-backed evidence.",
    )
    source = RuntimeSource(
        user_id=1,
        kind="text",
        source_uri="text://evidence.txt",
        content_hash=_sha("Evidence quote."),
        title="Evidence source",
        media_type="text/plain",
        status="indexed",
    )
    runtime_db.add_all([concept, source])
    runtime_db.flush()
    artifact = RuntimeSourceArtifact(
        user_id=1,
        source_id=source.id,
        artifact_kind="plain_text",
        content_text="Evidence quote.",
        content_hash=_sha("Evidence quote."),
    )
    runtime_db.add(artifact)
    runtime_db.flush()
    span = RuntimeSourceSpan(
        user_id=1,
        source_id=source.id,
        artifact_id=artifact.id,
        span_kind="paragraph",
        locator_json={"paragraph_index": 0},
        locator_hash=RuntimeSourceSpan.compute_locator_hash({"paragraph_index": 0}),
        content_text="Evidence quote.",
        content_hash=_sha("Evidence quote."),
    )
    runtime_db.add(span)
    runtime_db.flush()
    runtime_db.add(
        RuntimeKnowledgeConceptSource(
            user_id=1,
            concept_id=concept.id,
            source_id=source.id,
            span_id=span.id,
            citation_label="S1",
            quote_text="Evidence quote.",
            metadata_json={"reason": "supports"},
        )
    )
    runtime_db.commit()
    return concept


def test_export_writes_okf_bundle_layout_and_required_type(runtime_db, tmp_path) -> None:
    runtime_db.add(_concept())
    runtime_db.commit()

    result = export_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)

    concept_path = tmp_path / "concepts" / "topic-runtime-ingestion.md"
    assert result.concept_count == 1
    assert (tmp_path / "index.md").exists()
    assert (tmp_path / "log.md").exists()
    assert concept_path.exists()

    frontmatter, body = _read_frontmatter(concept_path)
    assert frontmatter["type"] == "topic"
    assert frontmatter["title"] == "Runtime ingestion"
    assert frontmatter["description"] == "A source-type-agnostic ingestion topic."
    assert frontmatter["resource"] == "https://example.test/resource"
    assert frontmatter["tags"] == ["ingestion", "okf"]
    assert frontmatter["timestamp"] == "2026-07-06T00:00:00+08:00"
    assert frontmatter["x_anima_unknown"] == {"kept": True}
    assert body == "Compiled notes.\n"


def test_export_rejects_concept_slugs_that_escape_concepts_dir(runtime_db, tmp_path) -> None:
    runtime_db.add(_concept(slug="../log", title="Unsafe path"))
    runtime_db.commit()

    with pytest.raises(ValueError, match="Unsafe OKF concept slug"):
        export_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)

    assert not (tmp_path / "log.md").exists()


def test_export_preserves_concept_citations(runtime_db, tmp_path) -> None:
    _seed_cited_concept(runtime_db)

    export_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)

    frontmatter, body = _read_frontmatter(tmp_path / "concepts" / "topic-citable.md")
    assert frontmatter["x_anima_citations"] == [
        {
            "citation_label": "S1",
            "source_uri": "text://evidence.txt",
            "source_title": "Evidence source",
            "source_kind": "text",
            "span_kind": "paragraph",
            "locator": {"paragraph_index": 0},
            "quote_text": "Evidence quote.",
            "metadata": {"reason": "supports"},
        }
    ]
    assert "## Source References" in body
    assert "- [S1] Evidence source (text://evidence.txt)" in body
    assert "> Evidence quote." in body


def test_import_strips_generated_source_references_from_exported_body(
    runtime_db,
    tmp_path,
) -> None:
    _seed_cited_concept(runtime_db)
    export_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)

    imported = import_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)
    runtime_db.commit()

    concept = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "topic-citable")
    )
    assert imported.concept_count == 1
    assert concept.body_markdown == "Compiled notes with source-backed evidence.\n"
    assert "## Source References" not in concept.body_markdown
    assert "x_anima_citations" not in concept.frontmatter_json


def test_import_round_trips_unknown_fields_and_unknown_types(runtime_db, tmp_path) -> None:
    concepts_dir = tmp_path / "concepts"
    concepts_dir.mkdir(parents=True)
    (tmp_path / "index.md").write_text("# Index\n", encoding="utf-8")
    (tmp_path / "log.md").write_text("# Log\n", encoding="utf-8")
    (concepts_dir / "custom-wiki-node.md").write_text(
        "---\n"
        "type: alien_concept\n"
        "title: Custom wiki node\n"
        "description: Imported description\n"
        "resource: file://source.md\n"
        "tags:\n"
        "  - custom\n"
        "timestamp: '2026-07-06T01:00:00+08:00'\n"
        "x_custom_field:\n"
        "  nested: 'yes'\n"
        "---\n\n"
        "Body with an unknown OKF type.\n",
        encoding="utf-8",
    )

    imported = import_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)
    runtime_db.commit()
    exported_dir = tmp_path / "roundtrip"
    exported = export_okf_bundle(runtime_db, user_id=1, bundle_dir=exported_dir)

    concept = runtime_db.scalar(select(RuntimeKnowledgeConcept))
    assert imported.concept_count == 1
    assert exported.concept_count == 1
    assert concept.concept_type == "alien_concept"
    assert concept.slug == "custom-wiki-node"
    assert concept.frontmatter_json["x_custom_field"] == {"nested": "yes"}

    frontmatter, body = _read_frontmatter(exported_dir / "concepts" / "custom-wiki-node.md")
    assert frontmatter["type"] == "alien_concept"
    assert frontmatter["x_custom_field"] == {"nested": "yes"}
    assert body == "Body with an unknown OKF type.\n"


def test_import_seals_private_slug_and_reuses_opaque_lookup(
    runtime_db,
    tmp_path,
    monkeypatch,
) -> None:
    from anima_server.services.corefs import sealed_runtime
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex

    concepts_dir = tmp_path / "concepts"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / "private-import.md").write_text(
        "---\n"
        "type: relationship-with-alex\n"
        "title: Private import\n"
        "---\n\n"
        "Private imported body.\n",
        encoding="utf-8",
    )
    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    monkeypatch.setattr(
        sealed_runtime,
        "_active_runtime_index",
        lambda _user_id: index,
    )

    import_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)
    import_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)
    runtime_db.flush()

    raw_concept = runtime_db.execute(
        select(
            RuntimeKnowledgeConcept.__table__.c.slug,
            RuntimeKnowledgeConcept.__table__.c.concept_type,
        )
    ).one()
    concept_ids = list(runtime_db.scalars(select(RuntimeKnowledgeConcept.id)))
    runtime_db.expunge_all()
    hydrated_concept = runtime_db.scalar(select(RuntimeKnowledgeConcept))

    assert raw_concept == (
        f"sealed:{index.blind_token('private-import').hex()}",
        f"sealed:{index.blind_token('relationship-with-alex').hex()}"[:48],
    )
    assert len(concept_ids) == 1
    assert hydrated_concept is not None
    assert hydrated_concept.slug == "private-import"
    assert hydrated_concept.concept_type == "relationship-with-alex"


def test_import_rejects_concept_slugs_that_export_would_reject(
    runtime_db,
    tmp_path,
) -> None:
    concepts_dir = tmp_path / "concepts"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / " topic-unsafe.md").write_text(
        "---\n"
        "type: topic\n"
        "title: Unsafe topic\n"
        "---\n\n"
        "Body with an unsafe filename stem.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsafe OKF concept slug"):
        import_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)

    assert list(runtime_db.scalars(select(RuntimeKnowledgeConcept)).all()) == []


def test_import_normalizes_yaml_timestamps_to_json_safe_strings(
    runtime_db,
    tmp_path,
) -> None:
    concepts_dir = tmp_path / "concepts"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / "dated-note.md").write_text(
        "---\n"
        "type: note\n"
        "title: Dated note\n"
        "published_on: 2026-07-06\n"
        "observed_at: 2026-07-06T01:00:00+08:00\n"
        "---\n\n"
        "Body with unquoted YAML timestamps.\n",
        encoding="utf-8",
    )

    imported = import_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)
    runtime_db.commit()

    concept = runtime_db.scalar(select(RuntimeKnowledgeConcept))
    assert imported.concept_count == 1
    assert concept.frontmatter_json["published_on"] == "2026-07-06"
    assert concept.frontmatter_json["observed_at"] == "2026-07-06T01:00:00+08:00"


def test_import_preserves_bundle_relative_links_and_creates_concept_links(
    runtime_db,
    tmp_path,
) -> None:
    concepts_dir = tmp_path / "concepts"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / "topic-a.md").write_text(
        "---\n"
        "type: topic\n"
        "title: Topic A\n"
        "---\n\n"
        "See [Topic B](topic-b.md) and keep the relative link.\n",
        encoding="utf-8",
    )
    (concepts_dir / "topic-b.md").write_text(
        "---\n"
        "type: topic\n"
        "title: Topic B\n"
        "---\n\n"
        "Target body.\n",
        encoding="utf-8",
    )

    result = import_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)
    runtime_db.commit()

    topic_a = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "topic-a")
    )
    topic_b = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.slug == "topic-b")
    )
    link = runtime_db.scalar(select(RuntimeKnowledgeLink))

    assert result.concept_count == 2
    assert result.link_count == 1
    assert "See [Topic B](topic-b.md)" in topic_a.body_markdown
    assert link.source_concept_id == topic_a.id
    assert link.target_concept_id == topic_b.id
    assert link.link_type == "related"


def test_import_deduplicates_repeated_relative_links(runtime_db, tmp_path) -> None:
    concepts_dir = tmp_path / "concepts"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / "topic-a.md").write_text(
        "---\n"
        "type: topic\n"
        "title: Topic A\n"
        "---\n\n"
        "See [Topic B](topic-b.md) and [Topic B again](topic-b.md#details).\n",
        encoding="utf-8",
    )
    (concepts_dir / "topic-b.md").write_text(
        "---\n"
        "type: topic\n"
        "title: Topic B\n"
        "---\n\n"
        "Target body.\n",
        encoding="utf-8",
    )

    result = import_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)

    assert result.concept_count == 2
    assert result.link_count == 1
    assert len(list(runtime_db.scalars(select(RuntimeKnowledgeLink)).all())) == 1


def test_import_replaces_only_prior_okf_links(runtime_db, tmp_path) -> None:
    source = _concept(slug="topic-existing", title="Existing topic")
    target = _concept(slug="topic-target", title="Target topic")
    stale_target = _concept(slug="topic-stale", title="Stale target")
    runtime_db.add_all([source, target, stale_target])
    runtime_db.flush()
    runtime_db.add_all(
        [
            RuntimeKnowledgeLink(
                user_id=1,
                source_concept_id=source.id,
                target_concept_id=target.id,
                link_type="supports",
                confidence=0.8,
                metadata_json={"source": "llm_compiler"},
            ),
            RuntimeKnowledgeLink(
                user_id=1,
                source_concept_id=source.id,
                target_concept_id=stale_target.id,
                link_type="related",
                confidence=1.0,
                metadata_json={"source": "okf_import"},
            ),
        ]
    )
    runtime_db.commit()

    concepts_dir = tmp_path / "concepts"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / "topic-existing.md").write_text(
        "---\n"
        "type: topic\n"
        "title: Existing topic\n"
        "---\n\n"
        "See [Target topic](topic-target.md).\n",
        encoding="utf-8",
    )
    (concepts_dir / "topic-target.md").write_text(
        "---\n"
        "type: topic\n"
        "title: Target topic\n"
        "---\n\n"
        "Target body.\n",
        encoding="utf-8",
    )

    result = import_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)

    links = list(
        runtime_db.scalars(
            select(RuntimeKnowledgeLink).order_by(RuntimeKnowledgeLink.link_type)
        ).all()
    )
    assert result.link_count == 1
    assert [(link.link_type, link.metadata_json["source"]) for link in links] == [
        ("related", "okf_import"),
        ("supports", "llm_compiler"),
    ]
    assert {link.target_concept_id for link in links} == {target.id}


def test_import_reuses_existing_non_okf_related_links(runtime_db, tmp_path) -> None:
    source = _concept(slug="topic-existing", title="Existing topic")
    target = _concept(slug="topic-target", title="Target topic")
    runtime_db.add_all([source, target])
    runtime_db.flush()
    runtime_db.add(
        RuntimeKnowledgeLink(
            user_id=1,
            source_concept_id=source.id,
            target_concept_id=target.id,
            link_type="related",
            confidence=0.7,
            metadata_json={"source": "llm_compiler"},
        )
    )
    runtime_db.commit()

    concepts_dir = tmp_path / "concepts"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / "topic-existing.md").write_text(
        "---\n"
        "type: topic\n"
        "title: Existing topic\n"
        "---\n\n"
        "See [Target topic](topic-target.md).\n",
        encoding="utf-8",
    )
    (concepts_dir / "topic-target.md").write_text(
        "---\n"
        "type: topic\n"
        "title: Target topic\n"
        "---\n\n"
        "Target body.\n",
        encoding="utf-8",
    )

    result = import_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)

    links = list(runtime_db.scalars(select(RuntimeKnowledgeLink)).all())
    assert result.link_count == 1
    assert len(links) == 1
    assert links[0].link_type == "related"
    assert links[0].metadata_json == {"source": "llm_compiler"}


def test_import_updates_existing_concept_by_slug(runtime_db, tmp_path) -> None:
    runtime_db.add(
        _concept(
            slug="topic-existing",
            title="Old title",
            body_markdown="Old body.",
            frontmatter_json={"type": "topic", "title": "Old title"},
        )
    )
    runtime_db.commit()
    concepts_dir = tmp_path / "concepts"
    concepts_dir.mkdir(parents=True)
    (concepts_dir / "topic-existing.md").write_text(
        "---\n"
        "type: decision\n"
        "title: New title\n"
        "---\n\n"
        "New body.\n",
        encoding="utf-8",
    )

    result = import_okf_bundle(runtime_db, user_id=1, bundle_dir=tmp_path)

    concepts = list(runtime_db.scalars(select(RuntimeKnowledgeConcept)).all())
    assert result.concept_count == 1
    assert len(concepts) == 1
    assert concepts[0].concept_type == "decision"
    assert concepts[0].title == "New title"
    assert concepts[0].body_markdown == "New body.\n"
    assert concepts[0].content_hash == _sha("New body.\n")
