"""Runtime document storage services."""

from anima_server.services.documents.indexing import (
    embed_document_chunks,
    get_unembedded_chunks,
)
from anima_server.services.documents.models import (
    DocumentRegistration,
    ExtractedDocumentChunk,
)
from anima_server.services.documents.rag import (
    DocumentRagResult,
    search_document_chunks,
)
from anima_server.services.documents.store import (
    DocumentStoragePathError,
    get_document_for_user,
    list_document_chunks,
    register_document,
    replace_document_chunks,
    resolve_document_storage_path,
    set_document_status,
)

__all__ = [
    "DocumentRagResult",
    "DocumentRegistration",
    "DocumentStoragePathError",
    "ExtractedDocumentChunk",
    "embed_document_chunks",
    "get_document_for_user",
    "get_unembedded_chunks",
    "list_document_chunks",
    "register_document",
    "replace_document_chunks",
    "resolve_document_storage_path",
    "search_document_chunks",
    "set_document_status",
]
