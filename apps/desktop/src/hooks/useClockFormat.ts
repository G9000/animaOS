import { useEffect, useState } from "react";

export type ClockFormat = "12h" | "24h";

const STORAGE_KEY = "anima_clock_format";
const EVENT_NAME = "anima:clockformat";

function read(): ClockFormat {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "12h" || v === "24h") return v;
  } catch {}
  return "24h";
}

export function useClockFormat() {
  const [format, setFormatState] = useState<ClockFormat>(read);

  useEffect(() => {
    const handler = (e: Event) => {
      setFormatState((e as CustomEvent<ClockFormat>).detail);
    };
    window.addEventListener(EVENT_NAME, handler);
    return () => window.removeEventListener(EVENT_NAME, handler);
  }, []);

  const setFormat = (f: ClockFormat) => {
    try {
      localStorage.setItem(STORAGE_KEY, f);
    } catch {}
    setFormatState(f);
    window.dispatchEvent(new CustomEvent<ClockFormat>(EVENT_NAME, { detail: f }));
  };

  return { format, setFormat };
}
