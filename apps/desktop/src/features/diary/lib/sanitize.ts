import createDOMPurify, { type Config, type WindowLike } from "dompurify";

const DIARY_HTML_CONFIG = {
  ALLOWED_TAGS: [
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
  ],
  ALLOWED_ATTR: ["alt", "class", "href", "rel", "src", "target", "title"],
  ALLOW_ARIA_ATTR: false,
  ALLOW_DATA_ATTR: false,
} satisfies Config;

export function createDiaryHtmlSanitizer(root: WindowLike): (html: string) => string {
  const purifier = createDOMPurify(root);

  return (html) => purifier.sanitize(html, DIARY_HTML_CONFIG) as string;
}
