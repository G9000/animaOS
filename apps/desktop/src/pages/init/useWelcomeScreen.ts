import { useState, useEffect } from "react";
import {
  greetingForLocale,
  resolveSystemLocale,
} from "../../lib/preUnlockEnvironment";

export function useWelcomeScreen() {
  const [welcomed, setWelcomed] = useState(false);
  const [helloVisible, setHelloVisible] = useState(false);
  const [hintVisible, setHintVisible] = useState(false);
  const [greeting] = useState(() => greetingForLocale(resolveSystemLocale()));

  // Staggered reveal of hello text and hint
  useEffect(() => {
    if (welcomed) return;
    const t1 = setTimeout(() => setHelloVisible(true), 700);
    const t2 = setTimeout(() => setHintVisible(true), 1600);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [welcomed]);

  const activeGreeting = !welcomed && helloVisible ? greeting : undefined;

  return {
    welcomed,
    hintVisible,
    activeGreeting,
    startProtocol: () => setWelcomed(true),
    setWelcomed,
  };
}
