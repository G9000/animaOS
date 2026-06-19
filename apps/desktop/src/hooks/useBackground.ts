import { useEffect, useState } from "react";
import {
  getBackgroundConfig,
  resolveBackgroundUrl,
  saveBackgroundConfig,
  saveBackgroundFile,
  DEFAULT_BACKGROUND,
  type BackgroundConfig,
  type BackgroundType,
} from "../lib/background";
import { BACKGROUND_CHANGED_EVENT } from "../lib/events";

export { DEFAULT_BACKGROUND, type BackgroundConfig, type BackgroundType };

export interface UseBackgroundResult {
  config: BackgroundConfig;
  url: string | null;
  set: (config: BackgroundConfig) => void;
  saveFile: (file: File) => Promise<string>;
  loading: boolean;
}

export function useBackground(): UseBackgroundResult {
  const [config, setConfigState] = useState<BackgroundConfig>(getBackgroundConfig);
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    resolveBackgroundUrl(config)
      .then((resolved) => { if (active) setUrl(resolved); })
      .catch(() => { if (active) setUrl(null); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [config]);

  useEffect(() => {
    const handler = () => setConfigState(getBackgroundConfig());
    window.addEventListener(BACKGROUND_CHANGED_EVENT, handler);
    return () => window.removeEventListener(BACKGROUND_CHANGED_EVENT, handler);
  }, []);

  const set = (next: BackgroundConfig) => {
    setConfigState(next);
    saveBackgroundConfig(next);
  };

  const saveFile = async (file: File): Promise<string> => {
    setLoading(true);
    try {
      const value = await saveBackgroundFile(file);
      return value;
    } finally {
      setLoading(false);
    }
  };

  return { config, url, set, saveFile, loading };
}
