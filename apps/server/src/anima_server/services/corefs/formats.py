from __future__ import annotations

import base64
import binascii
import hashlib
import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

DIARY_FORMAT_VERSION = 1
NOTE_FORMAT_VERSION = 1
DIARY_CONTENT_TYPE = "application/vnd.anima.diary+json;version=1"
NOTE_CONTENT_TYPE = "application/vnd.anima.note+json;version=1"
MAX_INLINE_MEDIA_BYTES = 10 * 1024 * 1024
ALLOWED_INLINE_MEDIA_TYPES = frozenset({"image/gif", "image/jpeg", "image/png", "image/webp"})
ALLOWED_DIARY_TAGS = frozenset(
    {
        "a",
        "blockquote",
        "br",
        "code",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "strong",
        "ul",
    }
)
ALLOWED_DIARY_ATTRIBUTES = frozenset({"alt", "class", "href", "rel", "src", "target", "title"})
_VOID_TAGS = frozenset({"br", "hr", "img"})
_DROP_CONTENT_TAGS = frozenset({"iframe", "object", "script", "style", "svg", "template"})
_DATA_IMAGE_RE = re.compile(
    r"^data:(image/[a-z0-9.+-]+);base64,([a-zA-Z0-9+/=\r\n]+)$",
    re.IGNORECASE,
)


class CoreFormatError(ValueError):
    """Raised when portable writing content cannot be made canonical."""


@dataclass(frozen=True, slots=True)
class CanonicalDiaryHtml:
    html: str
    media_uris: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiaryDocument:
    format_version: int
    stable_id: str
    entry_date: str
    title: str | None
    mood: str | None
    folder_id: str | None
    html: str
    cover_uri: str | None
    attachment_uris: tuple[str, ...]
    inline_media_uris: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NoteDocument:
    format_version: int
    stable_id: str
    title: str | None
    content_type: str
    body: str


MediaReferenceFactory = Callable[[str, bytes, str], str]


def canonicalize_diary_html(
    source: str,
    *,
    legacy_plain_text: bool = False,
    media_reference_factory: MediaReferenceFactory | None = None,
    max_inline_media_bytes: int = MAX_INLINE_MEDIA_BYTES,
) -> CanonicalDiaryHtml:
    if legacy_plain_text:
        source = "".join(
            f"<p>{html.escape(paragraph, quote=False)}</p>"
            for paragraph in re.split(r"(?:\r?\n){2,}", source)
            if paragraph
        )
    parser = _DiarySanitizer(max_inline_media_bytes=max_inline_media_bytes)
    try:
        parser.feed(source)
        parser.close()
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise CoreFormatError("Diary HTML contains malformed inline media.") from exc

    media_by_hash = parser.media_by_hash
    if media_by_hash and media_reference_factory is None:
        raise CoreFormatError("Inline diary media must be extracted before publication.")

    uris: dict[str, str] = {}
    for digest, (mime_type, data) in media_by_hash.items():
        assert media_reference_factory is not None
        uri = media_reference_factory(mime_type, data, digest)
        _validate_corefs_uri(uri)
        uris[digest] = uri

    sanitized = parser.render(uris)
    if "data:" in sanitized.lower():
        raise CoreFormatError("Canonical diary HTML cannot contain data URLs.")
    return CanonicalDiaryHtml(html=sanitized, media_uris=tuple(uris.values()))


def encode_diary_document(
    *,
    stable_id: str,
    entry_date: str,
    title: str | None,
    mood: str | None,
    folder_id: str | None,
    html: str,
    cover_uri: str | None,
    attachment_uris: tuple[str, ...] = (),
    media_reference_factory: MediaReferenceFactory | None = None,
    legacy_plain_text: bool = False,
) -> bytes:
    canonical = canonicalize_diary_html(
        html,
        legacy_plain_text=legacy_plain_text,
        media_reference_factory=media_reference_factory,
    )
    _validate_stable_id(stable_id)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry_date):
        raise CoreFormatError("Diary entry date must use YYYY-MM-DD.")
    references = tuple(attachment_uris)
    if cover_uri is not None:
        _validate_corefs_uri(cover_uri)
    for uri in references:
        _validate_corefs_uri(uri)
    payload = {
        "format": "anima.diary",
        "version": DIARY_FORMAT_VERSION,
        "stableId": stable_id,
        "entryDate": entry_date,
        "title": title,
        "mood": mood,
        "folderId": folder_id,
        "html": canonical.html,
        "coverUri": cover_uri,
        "attachmentUris": list(references),
        "inlineMediaUris": list(canonical.media_uris),
    }
    return _canonical_json(payload)


def decode_diary_document(data: bytes) -> DiaryDocument:
    payload = _decode_object(data, expected_format="anima.diary", version=DIARY_FORMAT_VERSION)
    stable_id = _required_string(payload, "stableId")
    _validate_stable_id(stable_id)
    html_body = _required_string(payload, "html", allow_empty=True)
    if "data:" in html_body.lower():
        raise CoreFormatError("Canonical diary HTML cannot contain data URLs.")
    attachment_uris = _string_tuple(payload, "attachmentUris")
    inline_media_uris = _string_tuple(payload, "inlineMediaUris")
    cover_uri = _optional_string(payload, "coverUri")
    for uri in (*attachment_uris, *inline_media_uris):
        _validate_corefs_uri(uri)
    if cover_uri is not None:
        _validate_corefs_uri(cover_uri)
    return DiaryDocument(
        format_version=DIARY_FORMAT_VERSION,
        stable_id=stable_id,
        entry_date=_required_string(payload, "entryDate"),
        title=_optional_string(payload, "title"),
        mood=_optional_string(payload, "mood"),
        folder_id=_optional_string(payload, "folderId"),
        html=html_body,
        cover_uri=cover_uri,
        attachment_uris=attachment_uris,
        inline_media_uris=inline_media_uris,
    )


def encode_note_document(
    *, stable_id: str, title: str | None, content_type: str, body: str
) -> bytes:
    _validate_stable_id(stable_id)
    if content_type == "text/html":
        body = canonicalize_diary_html(body).html
    elif content_type != "text/markdown":
        raise CoreFormatError("Notes must use Markdown or sanitized HTML.")
    payload = {
        "format": "anima.note",
        "version": NOTE_FORMAT_VERSION,
        "stableId": stable_id,
        "title": title,
        "contentType": content_type,
        "body": body,
    }
    return _canonical_json(payload)


def decode_note_document(data: bytes) -> NoteDocument:
    payload = _decode_object(data, expected_format="anima.note", version=NOTE_FORMAT_VERSION)
    content_type = _required_string(payload, "contentType")
    if content_type not in {"text/html", "text/markdown"}:
        raise CoreFormatError("Notes must use Markdown or sanitized HTML.")
    return NoteDocument(
        format_version=NOTE_FORMAT_VERSION,
        stable_id=_required_string(payload, "stableId"),
        title=_optional_string(payload, "title"),
        content_type=content_type,
        body=_required_string(payload, "body", allow_empty=True),
    )


class _DiarySanitizer(HTMLParser):
    def __init__(self, *, max_inline_media_bytes: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_inline_media_bytes = max_inline_media_bytes
        self.parts: list[str] = []
        self.media_by_hash: dict[str, tuple[str, bytes]] = {}
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth or tag not in ALLOWED_DIARY_TAGS:
            return
        clean_attrs: list[tuple[str, str]] = []
        for name, value in attrs:
            name = name.lower()
            if name not in ALLOWED_DIARY_ATTRIBUTES or value is None:
                continue
            if name in {"href", "src"}:
                value = self._safe_url(tag, name, value)
                if value is None:
                    continue
            if name == "target" and value not in {"_blank", "_self"}:
                continue
            clean_attrs.append((name, value))
        rendered_attrs = "".join(
            f' {name}="{html.escape(value, quote=True)}"' for name, value in clean_attrs
        )
        self.parts.append(f"<{tag}{rendered_attrs}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            if self._drop_depth:
                self._drop_depth -= 1
            return
        if not self._drop_depth and tag in ALLOWED_DIARY_TAGS and tag not in _VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._drop_depth:
            self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self._drop_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._drop_depth:
            self.parts.append(f"&#{name};")

    def _safe_url(self, tag: str, name: str, value: str) -> str | None:
        normalized = value.strip()
        if name == "src" and tag == "img" and normalized.lower().startswith("data:"):
            match = _DATA_IMAGE_RE.fullmatch(normalized)
            if match is None:
                raise CoreFormatError("Malformed inline diary image.")
            mime_type = match.group(1).lower()
            if mime_type not in ALLOWED_INLINE_MEDIA_TYPES:
                raise CoreFormatError("Unsupported inline diary image type.")
            data = base64.b64decode(match.group(2), validate=True)
            if not data or len(data) > self.max_inline_media_bytes:
                raise CoreFormatError("Inline diary image exceeds the allowed size.")
            digest = hashlib.sha256(data).hexdigest()
            self.media_by_hash.setdefault(digest, (mime_type, data))
            return f"__ANIMA_INLINE_MEDIA_{digest}__"
        parsed = urlsplit(normalized)
        if parsed.scheme.lower() in {"http", "https"}:
            return normalized
        if name == "href" and parsed.scheme.lower() == "mailto":
            return normalized
        if name == "src" and parsed.scheme.lower() == "corefs":
            return normalized
        if not parsed.scheme and not normalized.startswith("//"):
            return normalized
        return None

    def render(self, uris: dict[str, str]) -> str:
        output = "".join(self.parts)
        for digest, uri in uris.items():
            output = output.replace(f"__ANIMA_INLINE_MEDIA_{digest}__", uri)
        return output


def _validate_corefs_uri(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "corefs" or not parsed.netloc or not parsed.path.strip("/"):
        raise CoreFormatError("Portable media references must be stable CoreFS URIs.")


def _validate_stable_id(value: str) -> None:
    if not value or len(value) > 128 or any(character.isspace() for character in value):
        raise CoreFormatError("Portable writing objects require a stable ID.")


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _decode_object(data: bytes, *, expected_format: str, version: int) -> dict[str, Any]:
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoreFormatError("Portable writing object is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise CoreFormatError("Portable writing object must be a JSON object.")
    if payload.get("format") != expected_format or payload.get("version") != version:
        raise CoreFormatError("Unsupported portable writing format version.")
    return payload


def _required_string(payload: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise CoreFormatError(f"Portable writing field {key} must be a string.")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CoreFormatError(f"Portable writing field {key} must be a string or null.")
    return value


def _string_tuple(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CoreFormatError(f"Portable writing field {key} must be a string list.")
    return tuple(value)
