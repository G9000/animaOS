import { type ReactNode } from "react";
import { PromptInput, ChevronRightIcon, cn } from "@anima/standard-templates";

const glass = "bg-background/25 backdrop-blur-[40px] border border-foreground/[0.08] shadow-[0_20px_50px_-12px_rgba(0,0,0,0.28)]";

interface ChatLayoutProps {
  children: ReactNode;
  input: string;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  streaming: boolean;
  canSubmit?: boolean;
  inputAccessory?: ReactNode;
  onAttach?: (type: string) => void;
  sidebar?: ReactNode;
  showSidebar: boolean;
  onToggleSidebar: () => void;
  showScrollButton: boolean;
  onScrollToBottom: () => void;
  showTrace?: boolean;
  onToggleTrace?: () => void;
  scrollContainerRef?: React.RefObject<HTMLDivElement>;
  onScroll?: () => void;
}

export function ChatLayout({
  children,
  input,
  onInputChange,
  onSubmit,
  streaming,
  canSubmit = false,
  inputAccessory,
  onAttach,
  sidebar,
  showSidebar,
  onToggleSidebar,
  showScrollButton,
  onScrollToBottom,
  showTrace,
  onToggleTrace,
  scrollContainerRef,
  onScroll,
}: ChatLayoutProps) {
  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar */}
      {sidebar}

      {/* Main chat column */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        {/* Expand sidebar button */}
        {!showSidebar && (
          <button
            onClick={onToggleSidebar}
            title="Show threads"
            className={cn(glass, "absolute top-[84px] left-3 z-50 px-2 py-1 flex items-center justify-center text-foreground/40 hover:text-foreground transition-colors uppercase text-sm")}
          >
            Show threads
            <ChevronRightIcon size="sm" className="mt-0.5" />
          </button>
        )}

        {/* Messages scroll container */}
        <div
          ref={scrollContainerRef}
          onScroll={onScroll}
          className="flex-1 overflow-y-auto overscroll-contain px-3 md:px-5 lg:px-8 pt-20 scroll-smooth"
        >
          {/* Inner wrapper carries the bottom padding so scrollHeight includes it (avoids Chrome overflow+padding bug) */}
          <div className="max-w-5xl mx-auto w-full space-y-2 pb-[500px]">
            {children}
          </div>
        </div>

        {/* Scroll to bottom button */}
        {showScrollButton && (
          <button
            onClick={onScrollToBottom}
            className={cn(glass, "absolute left-1/2 -translate-x-1/2 bottom-24 md:bottom-28 z-20 font-mono text-[9px] px-3 py-1.5 text-foreground/50 hover:text-foreground transition-colors tracking-[0.2em] uppercase")}
          >
            LATEST ↓
          </button>
        )}

        {/* Floating input — absolute, overlays the scroll area */}
        <div className="absolute bottom-0 left-0 right-0 z-10 px-4 pt-8 pb-5 pointer-events-none">
          <div className="max-w-3xl mx-auto w-full pointer-events-auto">
            {inputAccessory}
            <PromptInput
              value={input}
              onChange={onInputChange}
              onSubmit={onSubmit}
              disabled={streaming}
              placeholder="type something..."
              showMic={true}
              canSubmit={canSubmit}
              onAttach={onAttach}
            />
            <div className="mt-2 h-4 flex items-center justify-center">
              {streaming && (
                <span className="font-mono text-[8px] text-accent/50 tracking-[0.2em] uppercase animate-pulse">
                  processing...
                </span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
