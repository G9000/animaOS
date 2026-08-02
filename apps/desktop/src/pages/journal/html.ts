import createDOMPurify, { type Config, type WindowLike } from "dompurify";
import sanitizerContract from "../../../../server/src/anima_server/services/corefs/writing-sanitizer-v1.json";

const DIARY_HTML_CONFIG = {
  ALLOWED_TAGS: sanitizerContract.allowedTags,
  ALLOWED_ATTR: sanitizerContract.allowedAttributes,
  ALLOW_ARIA_ATTR: false,
  ALLOW_DATA_ATTR: false,
} satisfies Config;

export const DIARY_SANITIZER_CONTRACT_ID = sanitizerContract.id;

export function createDiaryHtmlSanitizer(root: WindowLike): (html: string) => string {
  const purifier = createDOMPurify(root);

  return (html) => purifier.sanitize(html, DIARY_HTML_CONFIG) as string;
}
