import { Navigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { cn, Button, StandardInput, InfoIcon } from "@anima/standard-templates";
import { PersonaTemplateCards } from "../../components/PersonaTemplateCards";
import { S, TEMPLATES, HINTS } from "./constants";
import { useWelcomeScreen } from "./useWelcomeScreen";
import { useProtocolLines } from "./useProtocolLines";
import { useSetupMachine } from "./useSetupMachine";
import { RecoveryPhraseStep } from "./RecoveryPhraseStep";
import { InitFooter } from "./InitFooter";

export default function Init() {
  const { isProvisioned, setUser, user, isLoading } = useAuth();
  const { welcomed, hintVisible, startProtocol, setWelcomed } =
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

  const showInput =
    welcomed &&
    ready &&
    step !== S.AGENT_MODE &&
    step !== S.AGENT_INTRO &&
    step !== S.CONFIRM &&
    !done;

  const glass =
    "bg-background/20 backdrop-blur-[44px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.24)]";

  const hint = (text: string) => (
    <div className={cn(glass, "border-accent px-3 py-1.5 animate-fade-in flex items-center gap-2")}>
      <InfoIcon size="sm" className="text-accent shrink-0" />
      <p className="font-mono text-detail text-accent tracking-wide uppercase">{text}</p>
    </div>
  );

  const hasPendingPhrase = !!recoveryPhrase;
  if (welcomed && isProvisioned && !user && !isLoading && !done && !hasPendingPhrase)
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
            <div className="flex justify-center mb-6">
              <div className={glass}>
                <div className="bg-accent px-2">
                  <p
                    key={lastQuestion.id}
                    className="font-mono text-ui font-semibold tracking-[0.25em] text-foreground uppercase animate-fade-in"
                  >
                    {lastQuestion.text}
                  </p>
                </div>
              </div>
            </div>
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
                onHoverChange={() => {}}
                onBack={backToAgentName}
              />
            )}
          </div>
        </>
      );
    }

    if (showInput) {
      return (
        <div ref={bottomRef} className="flex flex-col items-center gap-2">
          <StandardInput
            ref={inputRef}
            label={lastQuestion?.text ?? ""}
            value={input}
            onChange={setInput}
            onSubmit={submit}
            onBack={goBack}
            password={cur.password}
            disabled={isRevealing}
            error={lastError?.revealed}
          />
          {HINTS[step] && hint(HINTS[step]!)}
        </div>
      );
    }

    if (step === S.CONFIRM && !done) {
      return (
        <div ref={bottomRef} className="flex flex-col items-center gap-3">
          {HINTS[S.CONFIRM] && hint(HINTS[S.CONFIRM]!)}
          <div className={glass}>
            {lastQuestion && (
              <div className="bg-accent px-2">
                <p
                  key={lastQuestion.id}
                  className="font-mono text-ui font-semibold tracking-[0.25em] text-foreground uppercase animate-fade-in"
                >
                  {lastQuestion.text}
                </p>
              </div>
            )}
            <Button
              size="xs"
              variant="main"
              onClick={confirmCreate}
              disabled={isRevealing}
              className="w-full h-12"
            >
              initialize →
            </Button>
          </div>
          <div className={glass}>
            <Button size="xs" variant="main" onClick={goBack} disabled={isRevealing}>
              ← go back
            </Button>
          </div>
        </div>
      );
    }

    return (
      <div ref={bottomRef} className="flex justify-center">
        <div className={glass}>
          {lastQuestion && (
            <div className="bg-accent px-2">
              <p
                key={lastQuestion.id}
                className="font-mono text-ui font-semibold tracking-[0.25em] text-foreground uppercase animate-fade-in"
              >
                {lastQuestion.text}
              </p>
            </div>
          )}
          {done ? (
            <div className="h-12 flex items-center px-4">
              <span className="font-mono text-caption text-subtle-foreground tracking-widest uppercase animate-pulse">
                [ initializing ]
              </span>
            </div>
          ) : (
            <div className="h-12 flex items-center px-4">
              <span className="font-mono text-subtle-foreground text-body animate-pulse">_</span>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "h-screen w-screen text-foreground flex flex-col justify-end overflow-hidden relative",
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

      <div
        className="absolute inset-0 pointer-events-none z-[1]"
        style={{
          background: "radial-gradient(ellipse 80% 50% at 50% 100%, rgba(0,0,0,0.82) 0%, transparent 100%)",
        }}
      />

      <div>
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
    </div>
  );
}
