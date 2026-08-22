const FALLBACK_LOCALE = "en-US";

const RTL_LANGUAGES = new Set([
  "ar",
  "dv",
  "fa",
  "he",
  "ku",
  "ps",
  "sd",
  "ug",
  "ur",
  "yi",
]);

const GREETINGS: Record<string, string> = {
  ar: "مرحبا",
  de: "hallo",
  en: "hello",
  es: "hola",
  fr: "bonjour",
  hi: "namaste",
  it: "ciao",
  ja: "こんにちは",
  ko: "안녕",
  ms: "selamat datang",
  ru: "привет",
  tr: "merhaba",
  zh: "你好",
  zu: "sawubona",
};

function canonicalLocale(candidate: string): string | null {
  try {
    return Intl.getCanonicalLocales(candidate)[0] ?? null;
  } catch {
    return null;
  }
}

export function resolveSystemLocale(
  languages: readonly string[] = navigator.languages,
  language: string = navigator.language,
): string {
  for (const candidate of [...languages, language]) {
    const canonical = canonicalLocale(candidate);
    if (canonical) return canonical;
  }
  return FALLBACK_LOCALE;
}

export function localeLanguage(locale: string): string {
  return locale.split("-", 1)[0]?.toLowerCase() || "en";
}

export function isRtlLocale(locale: string): boolean {
  return RTL_LANGUAGES.has(localeLanguage(locale));
}

export function greetingForLocale(locale: string): string {
  return GREETINGS[localeLanguage(locale)] ?? GREETINGS.en;
}

export function initializePreUnlockEnvironment(): () => void {
  const root = document.documentElement;
  const locale = resolveSystemLocale();
  root.lang = locale;
  root.dir = isRtlLocale(locale) ? "rtl" : "ltr";

  const preferences = [
    ["(prefers-reduced-motion: reduce)", "reducedMotion"],
    ["(prefers-contrast: more)", "highContrast"],
    ["(forced-colors: active)", "forcedColors"],
  ] as const;
  const cleanups = preferences.map(([query, key]) => {
    const media = window.matchMedia(query);
    const apply = () => {
      root.dataset[key] = String(media.matches);
    };
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  });
  return () => cleanups.forEach((cleanup) => cleanup());
}
