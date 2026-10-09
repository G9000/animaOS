"""Declared canonical original authority for deterministic document unit tests.

Only original lookup/authentication is mocked. Registration, source projection,
parsing outcome handling, chunks, vectors, and sealed Runtime writes stay real.
Parser bytes are provided by each test's parser double, never a host-file alias.
"""

from __future__ import annotations

from types import SimpleNamespace

from anima_server.services.corefs.catalog_reader import stable_opaque_id
from anima_server.services.documents.models import DocumentRegistration


def canonical_document_uri(user_id: int, digest: str) -> str:
    return f"corefs://object/{stable_opaque_id('fixture-document', f'{user_id}:{digest}')}"


class CanonicalDocumentOriginals:
    def __init__(self):
        from anima_server.services.documents.store import register_document

        self._register_document = register_document
        self._sessions = {}
        self._originals = {}

    def declare_owner(self, user_id):
        """Explicitly declare an empty current library before original registration."""
        return self._sessions.setdefault(user_id, SimpleNamespace(user_id=user_id))

    def register(self, db, registration: DocumentRegistration):
        assert registration.storage_path == canonical_document_uri(
            registration.user_id, registration.sha256
        )
        self._originals[registration.storage_path] = registration
        self.declare_owner(registration.user_id)
        return self._register_document(db, registration)

    def require_session(self, user_id):
        assert user_id in self._sessions, (
            "Original owner must be explicitly declared by the fixture"
        )
        return self._sessions[user_id]

    def open_source(self, *, session, object_uri, expected_kinds=None):
        assert object_uri in self._originals, (
            "Original URI must be explicitly declared by the fixture"
        )
        registration = self._originals[object_uri]
        assert session is self._sessions[registration.user_id]
        assert session.user_id == registration.user_id
        assert expected_kinds in (None, frozenset({"attachment"}))
        return SimpleNamespace(
            stable_id=object_uri.removeprefix("corefs://object/"),
            name=registration.filename,
            object_kind="attachment",
            content_type=registration.mime_type,
            content_sha256=registration.sha256,
            size=registration.size_bytes,
        )

    def install(self, monkeypatch):
        from anima_server.services.corefs import asset_authority
        from anima_server.services.documents import store

        monkeypatch.setattr(
            asset_authority, "require_canonical_asset_session", self.require_session
        )
        monkeypatch.setattr(asset_authority, "open_corefs_byte_source", self.open_source)
        monkeypatch.setattr(store, "register_document", self.register)
