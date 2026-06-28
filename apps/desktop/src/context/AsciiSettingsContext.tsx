import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

export interface AsciiSettings {
  cols: number;
  contrast: number;
  brightness: number;
  color: boolean;
  edgeDetect: boolean;
  glyphSet: string;
  randomizeGlyphs: boolean;
}

export interface AsciiSettingsCtx {
  settings: AsciiSettings;
  update: (patch: Partial<AsciiSettings>) => void;
  reset: () => void;
  /** Session-only — not persisted. Set when the user uploads a custom file. */
  srcOverride?: string;
  srcOverrideType?: "image" | "video";
  srcOverrideName?: string;
  setSrcOverride: (url?: string, type?: "image" | "video", name?: string) => void;
}

const DEFAULTS: AsciiSettings = {
  cols: 140,
  contrast: 1.15,
  brightness: -8,
  color: true,
  edgeDetect: false,
  glyphSet: "K·▪",
  randomizeGlyphs: false,
};

const Ctx = createContext<AsciiSettingsCtx>({
  settings: DEFAULTS,
  update: () => {},
  reset: () => {},
  setSrcOverride: () => {},
});

function load(): AsciiSettings {
  try {
    const raw = localStorage.getItem("anima_ascii_settings");
    return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

export function AsciiSettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<AsciiSettings>(load);
  const [srcOverride, setSrcOverrideState] = useState<string | undefined>();
  const [srcOverrideType, setSrcOverrideType] = useState<"image" | "video" | undefined>();
  const [srcOverrideName, setSrcOverrideName] = useState<string | undefined>();

  const update = useCallback((patch: Partial<AsciiSettings>) => {
    setSettings(prev => {
      const next = { ...prev, ...patch };
      try { localStorage.setItem("anima_ascii_settings", JSON.stringify(next)); } catch {}
      return next;
    });
  }, []);

  const setSrcOverride = useCallback((url?: string, type?: "image" | "video", name?: string) => {
    setSrcOverrideState(url);
    setSrcOverrideType(type);
    setSrcOverrideName(name);
  }, []);

  const reset = useCallback(() => {
    setSettings(DEFAULTS);
    try { localStorage.removeItem("anima_ascii_settings"); } catch {}
    setSrcOverrideState(undefined);
    setSrcOverrideType(undefined);
    setSrcOverrideName(undefined);
  }, []);

  return (
    <Ctx.Provider value={{ settings, update, reset, srcOverride, srcOverrideType, srcOverrideName, setSrcOverride }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAsciiSettings() {
  return useContext(Ctx);
}
