import { useEffect, useState } from "react";
import {
  getPortablePreference,
  PORTABLE_PREFERENCES_CHANGED_EVENT,
  setPortablePreference,
} from "../lib/portablePreferences";

export type ClockFormat = "12h" | "24h";

const EVENT_NAME = "anima:clockformat";

function read(): ClockFormat {
  return getPortablePreference<ClockFormat>("clockFormat", "24h");
}

export function useClockFormat() {
  const [format, setFormatState] = useState<ClockFormat>(read);

  useEffect(() => {
    const handler = (e: Event) => {
      setFormatState((e as CustomEvent<ClockFormat>).detail);
    };
    window.addEventListener(EVENT_NAME, handler);
    const refresh = () => setFormatState(read());
    globalThis.addEventListener(PORTABLE_PREFERENCES_CHANGED_EVENT, refresh);
    return () => {
      window.removeEventListener(EVENT_NAME, handler);
      globalThis.removeEventListener(PORTABLE_PREFERENCES_CHANGED_EVENT, refresh);
    };
  }, []);

  const setFormat = (f: ClockFormat) => {
    setPortablePreference("clockFormat", f);
    setFormatState(f);
    window.dispatchEvent(new CustomEvent<ClockFormat>(EVENT_NAME, { detail: f }));
  };

  return { format, setFormat };
}
