from __future__ import annotations

import logging
import unicodedata
from collections.abc import Collection

from anima_server.services import anima_core_bindings

logger = logging.getLogger(__name__)


DEFAULT_TEXT_LIMIT = 16_384
_VALID_SINGLE_CHARS = {"a", "i", "A", "I"}
_NON_SPACE_SCRIPT_KINDS = {"han", "hiragana", "katakana", "hangul", "thai"}
_COMMON_WORDS = {
    "a", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is", "it",
    "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we", "am", "are",
    "can", "did", "for", "get", "got", "had", "has", "her", "him", "his", "its",
    "let", "may", "nor", "not", "now", "off", "old", "one", "our", "out", "own",
    "ran", "run", "saw", "say", "see", "set", "she", "the", "too", "two", "use",
    "was", "way", "who", "why", "yet", "you", "all", "and", "any", "but", "few",
    "how", "man", "new", "per", "put", "via",
}


def _token_kind(ch: str) -> str | None:
    if not _is_token_char(ch):
        return None
    if ch.isascii():
        return "ascii"

    name = unicodedata.name(ch, "")
    if name.startswith("CJK UNIFIED IDEOGRAPH"):
        return "han"
    if name.startswith("HIRAGANA"):
        return "hiragana"
    if name.startswith("KATAKANA"):
        return "katakana"
    if name.startswith("HANGUL"):
        return "hangul"
    if name.startswith("THAI"):
        return "thai"
    if name.startswith("ARABIC"):
        return "arabic"
    return "word"


def _is_token_char(ch: str) -> bool:
    return ch.isalnum() or ch == "'"


def _emit_lexical_run(
    tokens: list[str],
    run: list[str],
    *,
    run_kind: str | None,
    stopwords: Collection[str],
    min_word_chars: int,
    ngram_size: int,
) -> None:
    if not run:
        return

    raw = "".join(run)
    token = raw.casefold()
    if (
        token not in stopwords
        and (
            len(token) >= min_word_chars
            or any(ch.isdigit() for ch in raw)
            or not raw.isascii()
        )
    ):
        tokens.append(token)

    if run_kind in _NON_SPACE_SCRIPT_KINDS and len(raw) > 1:
        for ch in raw:
            unigram = ch.casefold()
            if unigram not in stopwords:
                tokens.append(unigram)

    if raw.isascii() or len(raw) < ngram_size:
        return

    for index in range(0, len(raw) - ngram_size + 1):
        ngram = raw[index : index + ngram_size].casefold()
        if ngram not in stopwords:
            tokens.append(ngram)


def unicode_lexical_tokens(
    text: str,
    *,
    stopwords: Collection[str] = (),
    min_word_chars: int = 2,
    ngram_size: int = 2,
) -> list[str]:
    """Tokenize multilingual text for degraded lexical matching.

    Space-delimited languages keep word tokens. Non-ASCII runs also emit
    character n-grams so scripts without spaces can still match substrings.
    This is a fallback aid, not a replacement for semantic retrieval.
    """
    normalized_stopwords = {word.casefold() for word in stopwords}
    tokens: list[str] = []
    run: list[str] = []
    run_kind: str | None = None

    for ch in unicodedata.normalize("NFKC", text):
        kind = _token_kind(ch)
        if kind is not None and (run_kind is None or kind == run_kind):
            run.append(ch)
            run_kind = kind
            continue
        _emit_lexical_run(
            tokens,
            run,
            run_kind=run_kind,
            stopwords=normalized_stopwords,
            min_word_chars=min_word_chars,
            ngram_size=ngram_size,
        )
        run = [ch] if kind is not None else []
        run_kind = kind

    _emit_lexical_run(
        tokens,
        run,
        run_kind=run_kind,
        stopwords=normalized_stopwords,
        min_word_chars=min_word_chars,
        ngram_size=ngram_size,
    )
    return tokens


def _is_common_word(value: str) -> bool:
    return value.lower() in _COMMON_WORDS


def _is_valid_single_char(value: str) -> bool:
    return len(value) == 1 and value in _VALID_SINGLE_CHARS


def _is_purely_alpha(value: str) -> bool:
    return bool(value) and all(ch.isalpha() for ch in value)


def _alpha_len(value: str) -> int:
    return sum(1 for ch in value if ch.isalpha())


def _is_orphan(word: str) -> bool:
    return _alpha_len(word) == 1 and _is_purely_alpha(word) and not _is_valid_single_char(word)


def _is_short_fragment(word: str) -> bool:
    length = _alpha_len(word)
    return 2 <= length <= 3 and _is_purely_alpha(word) and not _is_common_word(word)


def _is_likely_suffix(word: str) -> bool:
    length = _alpha_len(word)
    return length == 4 and _is_purely_alpha(word) and not _is_common_word(word)


def _should_start_merge(word: str, next_word: str) -> bool:
    if not _is_purely_alpha(word) or not _is_purely_alpha(next_word):
        return False

    word_len = _alpha_len(word)
    next_len = _alpha_len(next_word)
    word_common = _is_common_word(word)
    next_common = _is_common_word(next_word)
    word_orphan = _is_orphan(word)
    next_orphan = _is_orphan(next_word)
    word_fragment = _is_short_fragment(word)
    next_fragment = _is_short_fragment(next_word)
    next_suffix = _is_likely_suffix(next_word)

    if word_orphan or next_orphan:
        return True
    if word_fragment and (next_fragment or next_orphan or next_suffix):
        return True
    if _is_valid_single_char(word) and next_len <= 3 and not next_common:
        return True
    return word_common and word_len <= 3 and (next_fragment or next_suffix)


def _should_continue_merge(current: str, next_word: str, had_short_fragment: bool) -> bool:
    if not had_short_fragment or not _is_purely_alpha(next_word):
        return False

    next_len = _alpha_len(next_word)
    if next_len <= 3:
        return True
    return next_len == 4 and not _is_common_word(next_word) and _alpha_len(current) <= 5


def _python_fix_pdf_spacing(text: str) -> str:
    if len(text) < 3 or " " not in text:
        return text

    words = text.split()
    if len(words) < 2:
        return text

    output: list[str] = []
    index = 0

    while index < len(words):
        word = words[index]
        if index + 1 < len(words) and _should_start_merge(word, words[index + 1]):
            merged = word + words[index + 1]
            had_short_fragment = (
                _is_short_fragment(word)
                or _is_short_fragment(words[index + 1])
                or _is_orphan(word)
                or _is_orphan(words[index + 1])
                or (_is_valid_single_char(word) and _alpha_len(words[index + 1]) <= 3)
            )
            index += 2

            while index < len(words) and _should_continue_merge(merged, words[index], had_short_fragment):
                if _is_short_fragment(words[index]) or _is_orphan(words[index]):
                    had_short_fragment = True
                merged += words[index]
                index += 1

            output.append(merged)
            continue

        output.append(word)
        index += 1

    return " ".join(output)


def _truncate_python_boundary(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False

    end = 0
    count = 0
    index = 0
    while index < len(text):
        if count >= limit:
            break
        end = index + 1
        index += 1
        count += 1
        while index < len(text) and unicodedata.combining(text[index]):
            end = index + 1
            index += 1

    if end == 0 and text:
        return text[0], True
    return text[:end], end < len(text)


def _python_normalize_text(text: str, limit: int) -> tuple[str, bool] | None:
    limit = max(1, limit)
    normalized = unicodedata.normalize("NFKC", text)

    cleaned: list[str] = []
    last_was_space = False
    last_was_newline = False

    for ch in normalized:
        if ch == "\r":
            ch = "\n"
        if ch == "\t":
            ch = " "
        if unicodedata.category(ch).startswith("C") and ch != "\n":
            continue

        if ch == "\n":
            if last_was_newline:
                continue
            while cleaned and cleaned[-1] == " ":
                cleaned.pop()
            cleaned.append("\n")
            last_was_newline = True
            last_was_space = False
            continue

        if ch.isspace():
            if last_was_space or (cleaned and cleaned[-1] == "\n"):
                continue
            cleaned.append(" ")
            last_was_space = True
            last_was_newline = False
            continue

        cleaned.append(ch)
        last_was_space = False
        last_was_newline = False

    trimmed = "".join(cleaned).strip(" \t\r\n")
    if not trimmed:
        return None

    return _truncate_python_boundary(trimmed, limit)


def _normalize_text(text: str, *, limit: int) -> str:
    if anima_core_bindings.rust_normalize_text is not None:
        try:
            result = anima_core_bindings.rust_normalize_text(text, limit)
            if result is not None:
                normalized, _truncated = result
                return normalized
        except Exception:
            logger.debug(
                "Rust text normalization failed; falling back to Python",
                exc_info=True,
            )

    result = _python_normalize_text(text, limit)
    if result is None:
        return ""
    normalized, _truncated = result
    return normalized


def prepare_memory_text(
    text: str,
    *,
    limit: int = DEFAULT_TEXT_LIMIT,
    apply_pdf_spacing: bool = False,
) -> str:
    if not isinstance(text, str):
        return ""

    prepared = text
    if apply_pdf_spacing:
        if anima_core_bindings.rust_fix_pdf_spacing is not None:
            try:
                prepared = anima_core_bindings.rust_fix_pdf_spacing(prepared)
            except Exception:
                logger.debug(
                    "Rust PDF spacing fix failed; falling back to Python",
                    exc_info=True,
                )
                prepared = _python_fix_pdf_spacing(prepared)
        else:
            prepared = _python_fix_pdf_spacing(prepared)
    # PDF spacing normalization is intentionally opt-in. Leave plain text untouched
    # when callers do not explicitly request spacing fixes.

    return _normalize_text(prepared, limit=limit)


def prepare_embedding_text(text: str, *, limit: int = DEFAULT_TEXT_LIMIT) -> str:
    return prepare_memory_text(text, limit=limit, apply_pdf_spacing=False)
