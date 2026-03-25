import { useState } from "react";
import { getTheme, toggleTheme, type Theme } from "../../lib/theme";
import { Button } from "@anima/standard-templates";
import pkg from "../../../package.json";

interface InitFooterProps {
  hintVisible: boolean;
  onBegin: () => void;
}

export function InitFooter({ hintVisible, onBegin }: InitFooterProps) {
  const [theme, setTheme] = useState<Theme>(getTheme);

  return (
    <div
      className="shrink-0 px-8 pb-10 relative z-10 flex items-end justify-between transition-opacity duration-700"
      style={{ opacity: hintVisible ? 1 : 0 }}
    >
      <div className="flex flex-col items-start gap-1.5">
        <span className="text-label font-mono text-muted-foreground tracking-widest uppercase">
          v{pkg.version}
        </span>
        <Button
          size="xs"
          variant="ghost"
          onClick={(e) => { e.stopPropagation(); setTheme(toggleTheme()); }}
        >
          {theme === "dark" ? "light" : "dark"}
        </Button>
      </div>

      <div className="flex flex-col items-center gap-1.5">
        <Button size="sm" onClick={onBegin}>
          BEGIN INITIALIZATION
        </Button>
        <span className="text-label font-mono text-muted-foreground tracking-widest uppercase">
          or press enter
        </span>
      </div>

      <Button
        size="xs"
        variant="ghost"
        icon={<span>↑</span>}
        iconPosition="right"
        onClick={(e) => e.stopPropagation()}
      >
        upload core
      </Button>
    </div>
  );
}
