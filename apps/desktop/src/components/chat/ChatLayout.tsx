import type { ReactNode } from "react";
import { PromptInput } from "@anima/standard-templates";

interface ChatLayoutProps {
  children: ReactNode;
  input: string;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  streaming: boolean;
  sidebar?: ReactNode;
  showSidebar: boolean;
  onToggleSidebar: () => void;
  showScrollButton: boolean;
  onScrollToBottom: () => void;
}

export function ChatLayout({
  children,
  input,
  onInputChange,
  onSubmit,
  streaming,
  sidebar,
  showSidebar,
  onToggleSidebar,
  showScrollButton,
  onScrollToBottom,
}: ChatLayoutProps) {
  return (
    <div className="flex h-full overflow-hidden">
      {/* Main chat column */}
      <div className="flex-1 flex flex-col min-w-0 relative bg-background">
        {/* Expand sidebar button (when hidden) */}
        {!showSidebar && (
          <div
            onClick={onToggleSidebar}
            className="absolute top-3 right-3 z-50 px-3 py-2 font-mono text-[9px] tracking-[0.2em] uppercase text-muted-foreground/60 hover:text-foreground hover:bg-card border border-border bg-background/80 backdrop-blur-sm rounded-none transition-all cursor-pointer select-none "
            title="Show threads"
            role="button"
          >
            THREADS ◀
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 overflow-y-auto overscroll-contain px-3 md:px-5 lg:px-8 py-5 md:py-8 pb-36 scroll-smooth">
          <div className="max-w-5xl mx-auto w-full space-y-2">
            {children}
          </div>
        </div>

        {/* Scroll to bottom button */}
        {showScrollButton && (
          <button
            onClick={onScrollToBottom}
            className="absolute right-3 md:right-6 bottom-24 md:bottom-28 z-20 font-mono text-[9px] px-3 py-2 border border-border bg-card text-muted-foreground hover:text-foreground transition-all tracking-[0.2em] uppercase rounded-none hover:active:translate-y-[1px]"
          >
            LATEST ↓
          </button>
        )}

        {/* Floating Input — chunky hardware bar */}
        <div className="absolute bottom-0 left-0 right-0 z-10 px-4 py-4 bg-gradient-to-t from-background via-background to-transparent pointer-events-none">
          <div className="max-w-3xl mx-auto w-full pointer-events-auto">
            <PromptInput
              value={input}
              onChange={onInputChange}
              onSubmit={onSubmit}
              disabled={streaming}
              placeholder="type something..."
              showMic={false}
            />
            <div className="mt-2 flex items-center justify-center gap-4">
              <span className="font-mono text-[8px] text-muted-foreground/25 tracking-[0.2em] uppercase">
                ENTER to send · SHIFT+ENTER for newline
              </span>
              {streaming && (
                <span className="font-mono text-[8px] text-accent/60 tracking-[0.2em] uppercase animate-pulse">
                  PROCESSING...
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Sidebar (thread list) */}
      {sidebar}
    </div>
  );
}
