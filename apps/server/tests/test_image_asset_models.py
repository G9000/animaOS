from __future__ import annotations

import hashlib

import pytest
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models.runtime import (
    RuntimeImageAnnotation,
    RuntimeImageAsset,
    RuntimeImageMessageLink,
    RuntimeMessage,
    RuntimeThread,
)
from sqlalchemy import ForeignKeyConstraint, inspect, select, text
from sqlalchemy.exc import IntegrityError

pytest_plugins = ("conftest_runtime",)


@pytest.fixture(autouse=True)
def _enable_foreign_keys_for_image_model_tests(runtime_db) -> None:
    runtime_db.execute(text("PRAGMA foreign_keys = ON"))


def _constraint_columns(model: type, name: str) -> tuple[str, ...]:
    constraint = next(
        constraint for constraint in model.__table__.constraints if constraint.name == name
    )
    return tuple(column.name for column in constraint.columns)


def _foreign_key_constraint(model: type, name: str) -> ForeignKeyConstraint:
    constraint = next(
        constraint for constraint in model.__table__.constraints if constraint.name == name
    )
    assert isinstance(constraint, ForeignKeyConstraint)
    return constraint


def _message(runtime_db, *, user_id: int = 1) -> RuntimeMessage:
    thread = RuntimeThread(user_id=user_id, title="Images")
    runtime_db.add(thread)
    runtime_db.flush()
    message = RuntimeMessage(
        thread_id=thread.id,
        user_id=user_id,
        sequence_id=1,
        role="user",
        content_text="look at this screenshot",
    )
    runtime_db.add(message)
    runtime_db.flush()
    return message


def _asset(runtime_db, *, user_id: int = 1, sha256: str | None = None) -> RuntimeImageAsset:
    digest = sha256 or hashlib.sha256(b"image-bytes").hexdigest()
    asset = RuntimeImageAsset(
        user_id=user_id,
        filename="screenshot.png",
        mime_type="image/png",
        storage_path=f"users/{user_id}/media/images/{digest[:2]}/{digest}.png",
        sha256=digest,
        size_bytes=123,
        width=640,
        height=480,
        metadata_json={"origin": "chat"},
    )
    runtime_db.add(asset)
    runtime_db.flush()
    return asset


def test_image_asset_tables_are_registered(runtime_engine) -> None:
    RuntimeBase.metadata.create_all(runtime_engine)

    names = set(inspect(runtime_engine).get_table_names())

    assert RuntimeImageAsset.__tablename__ in names
    assert RuntimeImageMessageLink.__tablename__ in names
    assert RuntimeImageAnnotation.__tablename__ in names


def test_image_asset_hash_is_unique_per_user_constraint_registered() -> None:
    assert _constraint_columns(RuntimeImageAsset, "uq_runtime_image_assets_user_sha256") == (
        "user_id",
        "sha256",
    )


def test_image_message_link_is_unique_per_message_attachment_constraint_registered() -> None:
    assert _constraint_columns(
        RuntimeImageMessageLink,
        "uq_runtime_image_message_links_message_attachment",
    ) == ("message_id", "attachment_id")


def test_image_annotation_is_unique_per_asset_kind_hash_constraint_registered() -> None:
    assert _constraint_columns(
        RuntimeImageAnnotation,
        "uq_runtime_image_annotations_asset_kind_hash",
    ) == ("image_asset_id", "annotation_kind", "content_hash")


def test_image_links_cascade_when_message_is_deleted(runtime_db) -> None:
    message = _message(runtime_db)
    asset = _asset(runtime_db)
    link = RuntimeImageMessageLink(
        user_id=1,
        message_id=message.id,
        image_asset_id=asset.id,
        attachment_id="img_1",
    )
    runtime_db.add(link)
    runtime_db.flush()

    runtime_db.delete(message)
    runtime_db.flush()

    assert runtime_db.scalar(select(RuntimeImageMessageLink)) is None
    assert runtime_db.get(RuntimeImageAsset, asset.id) is not None


def test_image_annotations_cascade_when_asset_is_deleted(runtime_db) -> None:
    asset = _asset(runtime_db)
    annotation = RuntimeImageAnnotation(
        user_id=1,
        image_asset_id=asset.id,
        annotation_kind="upload_context",
        content_text="User uploaded screenshot.png while asking about layout.",
        content_hash=RuntimeImageAnnotation.compute_content_hash(
            "User uploaded screenshot.png while asking about layout."
        ),
    )
    runtime_db.add(annotation)
    runtime_db.flush()

    runtime_db.delete(asset)
    runtime_db.flush()

    assert runtime_db.scalar(select(RuntimeImageAnnotation)) is None


def test_image_assets_are_deduped_by_user_and_checksum(runtime_db) -> None:
    _asset(runtime_db, user_id=1, sha256="a" * 64)
    runtime_db.add(
        RuntimeImageAsset(
            user_id=1,
            filename="duplicate.png",
            mime_type="image/png",
            storage_path=f"users/1/media/images/aa/{'a' * 64}.png",
            sha256="a" * 64,
            size_bytes=123,
        )
    )

    with pytest.raises(IntegrityError):
        runtime_db.flush()


def test_same_checksum_can_belong_to_different_users(runtime_db) -> None:
    first = _asset(runtime_db, user_id=1, sha256="b" * 64)
    second = _asset(runtime_db, user_id=2, sha256="b" * 64)

    assert first.id != second.id
    assert first.sha256 == second.sha256


def test_message_link_enforces_asset_owner(runtime_db) -> None:
    message = _message(runtime_db, user_id=1)
    asset = _asset(runtime_db, user_id=2)
    runtime_db.add(
        RuntimeImageMessageLink(
            user_id=1,
            message_id=message.id,
            image_asset_id=asset.id,
            attachment_id="cross_user",
        )
    )

    with pytest.raises(IntegrityError):
        runtime_db.flush()


def test_annotation_enforces_asset_owner(runtime_db) -> None:
    asset = _asset(runtime_db, user_id=2)
    runtime_db.add(
        RuntimeImageAnnotation(
            user_id=1,
            image_asset_id=asset.id,
            annotation_kind="metadata",
            content_text="image/png, 123 bytes",
            content_hash=RuntimeImageAnnotation.compute_content_hash("image/png, 123 bytes"),
        )
    )

    with pytest.raises(IntegrityError):
        runtime_db.flush()


def test_message_link_ownership_foreign_key_constraint_registered() -> None:
    constraint = _foreign_key_constraint(
        RuntimeImageMessageLink,
        "fk_runtime_image_message_links_asset_user",
    )

    assert tuple(element.parent.name for element in constraint.elements) == (
        "image_asset_id",
        "user_id",
    )
    assert tuple(
        f"{element.column.table.name}.{element.column.name}" for element in constraint.elements
    ) == (
        "runtime_image_assets.id",
        "runtime_image_assets.user_id",
    )
    assert constraint.ondelete == "CASCADE"


def test_annotation_ownership_foreign_key_constraint_registered() -> None:
    constraint = _foreign_key_constraint(
        RuntimeImageAnnotation,
        "fk_runtime_image_annotations_asset_user",
    )

    assert tuple(element.parent.name for element in constraint.elements) == (
        "image_asset_id",
        "user_id",
    )
    assert tuple(
        f"{element.column.table.name}.{element.column.name}" for element in constraint.elements
    ) == (
        "runtime_image_assets.id",
        "runtime_image_assets.user_id",
    )
    assert constraint.ondelete == "CASCADE"
