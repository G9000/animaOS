import { useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import {
  useAnimaSymbol,
  useGlowLine,
  useAsciiText,
  cn,
  Button,
} from "@anima/standard-templates";
import { PersonaTemplateCards } from "../../components/PersonaTemplateCards";
import { S, TEMPLATES } from "./constants";
import { useWelcomeScreen } from "./useWelcomeScreen";
import { useProtocolLines } from "./useProtocolLines";
import { useSetupMachine } from "./useSetupMachine";
import { RecoveryPhraseStep } from "./RecoveryPhraseStep";
import { InitFooter } from "./InitFooter";
import { TerminalInput } from "./TerminalInput";

export default function Init() {
  const { isProvisioned, setUser, user, isLoading } = useAuth();

  const { welcomed, hintVisible, activeGreeting, startProtocol, setWelcomed } =
    useWelcomeScreen();
  const {
    lines,
    addLine,
    trimLines,
    isRevealing,
    lastQuestion,
    lastError,
    bottomRef,
  } = useProtocolLines();

  const {
    step,
    input,
    setInput,
    ready,
    done,
    recoveryPhrase,
    savingProfile,
    cur,
    inputRef,
    submit,
    goBack,
    confirmCreate,
    handleModeSelect,
    advanceFromRecovery,
    backToAgentName,
  } = useSetupMachine({
    welcomed,
    setWelcomed,
    addLine,
    trimLines,
    lines,
    isRevealing,
    isProvisioned,
    user,
    isLoading,
    setUser,
  });

  const [isFocused, setIsFocused] = useState(false);
  const [modeHovered, setModeHovered] = useState(false);

  const showInput =
    welcomed &&
    ready &&
    step !== S.AGENT_MODE &&
    step !== S.AGENT_INTRO &&
    step !== S.CONFIRM &&
    !done;

  const symbolSpeed = (() => {
    if (done) return 4;
    if (!welcomed) return 0.6;
    if (modeHovered || input.length > 0) return 2.5;
    if (isFocused) return 1.6;
    return 1;
  })();

  const animaSymbol = useAnimaSymbol(symbolSpeed, activeGreeting);
  const glowLine = useGlowLine(isFocused, isFocused ? 52 : 28);
  const questionText = lastQuestion?.text ?? "";
  const asciiQuestion = useAsciiText(
    questionText,
    !isRevealing && !!lastQuestion,
  );

  if (isProvisioned && !user && !isLoading && !done)
    return <Navigate to="/login" replace />;

  function renderBottom() {
    if (step === S.RECOVERY && recoveryPhrase) {
      return (
        <RecoveryPhraseStep
          phrase={recoveryPhrase}
          onContinue={advanceFromRecovery}
          bottomRef={bottomRef}
        />
      );
    }

    if (step === S.AGENT_MODE) {
      return (
        <>
          {lastQuestion && (
            <pre
              key={lastQuestion.id}
              className="font-mono text-ui mb-6 animate-fade-in text-center text-muted-foreground whitespace-pre-wrap uppercase"
            >
              {asciiQuestion}
            </pre>
          )}
          <div ref={bottomRef}>
            {savingProfile ? (
              <div className="flex gap-1.5 justify-center items-center animate-fade-in">
                <span className="w-1 h-1 bg-text-subtle animate-pulse" />
                <span className="w-1 h-1 bg-text-subtle animate-pulse [animation-delay:150ms]" />
                <span className="w-1 h-1 bg-text-subtle animate-pulse [animation-delay:300ms]" />
              </div>
            ) : (
              <PersonaTemplateCards
                templates={TEMPLATES}
                onSelect={handleModeSelect}
                onHoverChange={setModeHovered}
                onBack={backToAgentName}
              />
            )}
          </div>
        </>
      );
    }

    return (
      <div ref={bottomRef} className="flex flex-col items-center gap-4">
        {lastQuestion && (
          <pre
            key={lastQuestion.id}
            className="font-mono text-ui animate-fade-in text-center text-muted-foreground whitespace-pre-wrap uppercase"
          >
            {asciiQuestion}
          </pre>
        )}

        {/* Error */}
        {lastError && (
          <div
            key={lastError.id}
            className="font-mono text-detail text-subtle-foreground animate-fade-in text-center"
          >
            [err] {lastError.revealed}
          </div>
        )}

        {/* Input / states */}
        <div className="animate-fade-in w-full" key={step}>
          {step === S.CONFIRM && !done ? (
            <div className="flex flex-col items-center gap-3 animate-fade-in">
              <Button size="sm" onClick={confirmCreate} disabled={isRevealing}>
                initialize
              </Button>
              <Button size="xs" variant="ghost" onClick={goBack} disabled={isRevealing}>
                ← go back
              </Button>
            </div>
          ) : showInput ? (
            <TerminalInput
              inputRef={inputRef}
              value={input}
              onChange={setInput}
              onSubmit={submit}
              onBack={goBack}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setIsFocused(false)}
              placeholder=""
              password={cur.password}
              disabled={isRevealing}
              isFocused={isFocused}
              glowLine={glowLine}
            />
          ) : done ? (
            <div className="text-center">
              <span className="font-mono text-caption text-subtle-foreground tracking-widest uppercase animate-pulse">
                [ initializing ]
              </span>
            </div>
          ) : (
            <div className="text-center">
              <span className="font-mono text-subtle-foreground text-body animate-pulse">
                _
              </span>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "h-screen w-screen bg-background text-foreground flex flex-col overflow-hidden relative",
        !welcomed ? "cursor-default" : "text-ui",
      )}
      onClick={
        welcomed
          ? () => {
              if (step !== S.AGENT_MODE && step !== S.AGENT_INTRO)
                inputRef.current?.focus();
            }
          : undefined
      }
      tabIndex={!welcomed ? 0 : undefined}
      onKeyDown={
        !welcomed
          ? (e) => {
              if (e.key === "Enter") startProtocol();
            }
          : undefined
      }
    >
      <div className="absolute inset-0 pointer-events-none" />

      <div className="flex-1 flex items-center justify-center pointer-events-none min-h-0 relative z-10">
        <div
          className={cn(
            "relative origin-center transition-transform duration-700 ease-out",
            welcomed
              ? "scale-[0.45] sm:scale-[0.65]"
              : "scale-[0.6] sm:scale-100",
          )}
        >
          <pre className="text-body whitespace-pre leading-none text-foreground/50 bg-transparent">
            {animaSymbol.base}
          </pre>
          {activeGreeting && (
            <pre className="text-body whitespace-pre leading-none text-foreground/80 absolute inset-0 bg-transparent">
              {animaSymbol.text}
            </pre>
          )}
        </div>
      </div>

      {!welcomed ? (
        <InitFooter hintVisible={hintVisible} onBegin={startProtocol} />
      ) : (
        <div className="shrink-0 px-8 pb-8 relative z-10">
          <div className="w-full max-w-2xl mx-auto font-mono text-sm">
            {renderBottom()}
          </div>
        </div>
      )}
    </div>
  );
}
