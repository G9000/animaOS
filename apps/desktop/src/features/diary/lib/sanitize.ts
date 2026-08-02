import createDOMPurify, { type Config, type WindowLike } from "dompurify";

// `style` and `input` are permanently excluded. Tiptap serializes table
// widths as inline style and task checkboxes as <input>; both are dropped
// deliberately. Checked state survives on the <li> via data-checked, so the
// round-trip is lossless. Column widths do not persist — that is accepted.
const DIARY_HTML_CONFIG = {
  ALLOWED_TAGS: [
    "a",
    "blockquote",
    "br",
    "code",
    "col",
    "colgroup",
    "details",
    "div",
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
    "mark",
    "ol",
    "p",
    "pre",
    "s",
    "span",
    "strong",
    "summary",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
  ],
  ALLOWED_ATTR: [
    "alt",
    "class",
    "colspan",
    "colwidth",
    "data-attachment-id",
    "data-checked",
    "data-tone",
    "data-type",
    "href",
    "rel",
    "rowspan",
    "src",
    "target",
    "title",
  ],
  ALLOW_ARIA_ATTR: false,
  ALLOW_DATA_ATTR: false,
} satisfies Config;

export function createDiaryHtmlSanitizer(root: WindowLike): (html: string) => string {
  const purifier = createDOMPurify(root);

  return (html) => purifier.sanitize(html, DIARY_HTML_CONFIG) as string;
}
