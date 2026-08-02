import createDOMPurify, { type Config, type WindowLike } from "dompurify";
import sanitizerContract from "../../../../server/src/anima_server/services/corefs/writing-sanitizer-v1.json";

const DIARY_HTML_CONFIG = {
  ALLOWED_TAGS: sanitizerContract.allowedTags,
  ALLOWED_ATTR: sanitizerContract.allowedAttributes,
  ALLOW_ARIA_ATTR: false,
  ALLOW_DATA_ATTR: false,
  // URI-bearing attributes are governed by the shared contract hook below.
  ALLOW_UNKNOWN_PROTOCOLS: true,
} satisfies Config;

const URI_SCHEME = /^([a-z][a-z0-9+.-]*):/i;
const INLINE_IMAGE_DATA = /^data:(image\/[a-z0-9.+-]+);base64,([a-z0-9+/=\r\n]+)$/i;
const STRICT_BASE64 = /^(?:[a-z0-9+/]{4})*(?:[a-z0-9+/]{2}==|[a-z0-9+/]{3}=)?$/i;

function validInlineImageData(value: string): boolean {
  const match = INLINE_IMAGE_DATA.exec(value);
  if (!match || !sanitizerContract.allowedInlineMediaTypes.includes(match[1].toLowerCase())) {
    return false;
  }
  const encoded = match[2].replace(/[\r\n]/g, "");
  if (!encoded || !STRICT_BASE64.test(encoded)) {
    return false;
  }
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const decodedBytes = (encoded.length / 4) * 3 - padding;
  return decodedBytes > 0 && decodedBytes <= sanitizerContract.maxInlineMediaBytes;
}

function allowedWritingUri(tag: string, attribute: "href" | "src", rawValue: string): boolean {
  const value = rawValue.trim();
  const scheme = URI_SCHEME.exec(value)?.[1].toLowerCase();
  if (scheme === "data") {
    return sanitizerContract.uriPolicy.data.desktopAction === "preserve-supported-image"
      && attribute === "src"
      && tag === "img"
      && validInlineImageData(value);
  }
  if (scheme) {
    const allowed = attribute === "href"
      ? sanitizerContract.uriPolicy.allowedHrefSchemes
      : sanitizerContract.uriPolicy.allowedSrcSchemes;
    return allowed.includes(scheme);
  }
  if (value.startsWith("//")) {
    return sanitizerContract.uriPolicy.allowSchemeRelative;
  }
  return sanitizerContract.uriPolicy.allowRelative;
}

export const DIARY_SANITIZER_CONTRACT_ID = sanitizerContract.id;

export function createDiaryHtmlSanitizer(root: WindowLike): (html: string) => string {
  const purifier = createDOMPurify(root);
  purifier.addHook("uponSanitizeAttribute", (node, data) => {
    if (data.attrName !== "href" && data.attrName !== "src") {
      return;
    }
    const value = data.attrValue.trim();
    if (!allowedWritingUri(node.nodeName.toLowerCase(), data.attrName, value)) {
      data.keepAttr = false;
      return;
    }
    data.attrValue = value;
  });

  return (html) => purifier.sanitize(html, DIARY_HTML_CONFIG) as string;
}
