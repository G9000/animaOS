import { useEffect, useState } from "react";
import { useTheme } from "../../hooks/useTheme";
import { Button, glass } from "@anima/standard-templates";
import pkg from "../../../package.json";

interface InitFooterProps {
  hintVisible: boolean;
  onBegin: () => void;
}

export function InitFooter({ hintVisible, onBegin }: InitFooterProps) {
  const { effective: theme, toggle: toggleTheme } = useTheme();
  const [spaceHeld, setSpaceHeld] = useState(false);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.code === "Space" && !e.repeat) {
        e.preventDefault();
        setSpaceHeld(true);
      }
    }
    function onKeyUp(e: KeyboardEvent) {
      if (e.code !== "Space") return;
      setSpaceHeld(false);
      onBegin();
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [onBegin]);

  return (
    <div
      className="shrink-0 px-8 pb-10 relative z-10 flex items-end justify-between transition-opacity duration-700"
      style={{ opacity: hintVisible ? 1 : 0 }}
    >
      <div className="flex flex-col items-start gap-1.5">
        <span className="text-label font-mono text-accent tracking-widest uppercase">
          v{pkg.version}
        </span>
        <div className={glass}>
          <Button
            size="xs"
            variant="main"
            onClick={(e) => { e.stopPropagation(); toggleTheme(); }}
          >
            {theme === "dark" ? "light" : "dark"}
          </Button>
        </div>
      </div>

      <div className="flex flex-col items-center gap-1.5">
        <div className={glass}>
          <Button
            size="xl"
            variant="main"
            onClick={onBegin}
            className={spaceHeld ? "font-bold btn-force-hover" : "font-bold"}
          >
            BEGIN INITIALIZATION
          </Button>
        </div>
        <span className="text-base font-mono text-accent tracking-widest uppercase inline-flex items-center gap-1.5">
          or press
          <kbd
            className="inline-flex items-center border border-current rounded-sm px-1.5 py-px text-[0.65em] leading-none"
            style={{ letterSpacing: 0, borderBottomWidth: 2 }}
          >
            SPACE
          </kbd>
        </span>
      </div>

      <div className={glass}>
        <Button
          size="xs"
          variant="main"
          icon={<span>↑</span>}
          iconPosition="right"
          onClick={(e) => e.stopPropagation()}
        >
          upload core
        </Button>
      </div>
    </div>
  );
}
