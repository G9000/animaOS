from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from anima_server.models.runtime import RuntimeKnowledgeConcept, RuntimeKnowledgeLink
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
