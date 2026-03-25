import { useState, useEffect } from "react";
import { GREETINGS } from "./constants";

export function useWelcomeScreen() {
  const [welcomed, setWelcomed] = useState(false);
  const [helloVisible, setHelloVisible] = useState(false);
  const [hintVisible, setHintVisible] = useState(false);
  const [greetingIdx, setGreetingIdx] = useState(0);
  const [greetingFading, setGreetingFading] = useState(false);

  // Staggered reveal of hello text and hint
  useEffect(() => {
    if (welcomed) return;
    const t1 = setTimeout(() => setHelloVisible(true), 700);
    const t2 = setTimeout(() => setHintVisible(true), 1600);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [welcomed]);

  // Cycle through greetings in different languages
  useEffect(() => {
    if (welcomed) return;
    let fadeOut: ReturnType<typeof setTimeout>;
    const interval = setInterval(() => {
      setGreetingFading(true);
      fadeOut = setTimeout(() => {
        setGreetingIdx((i) => (i + 1) % GREETINGS.length);
        setGreetingFading(false);
      }, 400);
    }, 2800);
    return () => { clearInterval(interval); clearTimeout(fadeOut); };
  }, [welcomed]);

  const activeGreeting = !welcomed && helloVisible && !greetingFading
    ? GREETINGS[greetingIdx]
    : undefined;

  return {
    welcomed,
    hintVisible,
    activeGreeting,
    startProtocol: () => setWelcomed(true),
    setWelcomed,
  };
}
