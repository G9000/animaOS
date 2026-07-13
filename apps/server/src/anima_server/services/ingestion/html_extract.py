"""Readable-article extraction from raw HTML.

trafilatura strips navigation/boilerplate and emits markdown with headings,
lists, and pipe tables, so extracted pages flow through the same
structured-markdown pipeline as native markdown sources.
"""

from __future__ import annotations

from dataclasses import dataclass

HTML_EXTRACTOR_NAME = "trafilatura"


@dataclass(frozen=True, slots=True)
class HtmlExtraction:
    markdown: str
    title: str | None = None
    author: str | None = None
    date: str | None = None
    canonical_url: str | None = None
    sitename: str | None = None


def extract_html_article(html: str, *, url: str | None = None) -> HtmlExtraction:
    """Extract the readable article from *html* as structured markdown.

    Raises ValueError when no readable content can be found (e.g. an empty
    page or pure boilerplate).
    """
    # Heavy import (~1s with lxml); only pay it when HTML is actually ingested.
    import trafilatura

    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_images=False,
        include_tables=True,
    )
    if not markdown or not markdown.strip():
        raise ValueError("No readable article content found in the HTML.")

    try:
        metadata = trafilatura.extract_metadata(html, default_url=url)
    except Exception:
        metadata = None

    def _field(name: str) -> str | None:
        value = getattr(metadata, name, None)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    return HtmlExtraction(
        markdown=markdown.strip(),
        title=_field("title"),
        author=_field("author"),
        date=_field("date"),
        canonical_url=_field("url"),
        sitename=_field("sitename"),
    )


__all__ = ["HTML_EXTRACTOR_NAME", "HtmlExtraction", "extract_html_article"]
