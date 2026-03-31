import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api, setUnlockToken } from "../../lib/api";
import type { PersonaCardData } from "../../components/PersonaTemplateCards";
import { S, STEPS, TEMPLATES, COPY } from "./constants";
import type { AddLineFn } from "./useProtocolLines";

interface SetupMachineDeps {
  welcomed: boolean;
  setWelcomed: (v: boolean) => void;
  addLine: AddLineFn;
  trimLines: (n: number) => void;
  lines: { length: number };
  isRevealing: boolean;
  isProvisioned: boolean;
  user: { id: number; username: string; name: string } | null;
  isLoading: boolean;
  setUser: (u: { id: number; username: string; name: string }) => void;
}

export function useSetupMachine({
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
}: SetupMachineDeps) {
  const navigate = useNavigate();

  const [input, setInput] = useState("");
  const [step, setStep] = useState<number>(S.NAME);
  const [ready, setReady] = useState(false);
  const [data, setData] = useState({ name: "", username: "", password: "" });
  const [done, setDone] = useState(false);
  const [recoveryPhrase, setRecoveryPhrase] = useState<string | null>(null);
  const [pendingUser, setPendingUser] = useState<{ id: number; username: string; name: string } | null>(null);
  const [agentName, setAgentName] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const stepSnapshots = useRef(new Map<number, number>());
  const setupOnlyStartedRef = useRef(false);

  const cur = STEPS[step];

  // ── Effects ────────────────────────────────────────────────────────────

  // First question after welcome
  useEffect(() => {
    if (!welcomed || ready) return;
    addLine("output", COPY.askName);
    setReady(true);
  }, [welcomed, ready, addLine]);

  // Auto-focus input
  useEffect(() => {
    if (welcomed && ready && !done && !isRevealing && step !== S.AGENT_MODE && step !== S.AGENT_INTRO) {
      inputRef.current?.focus();
    }
  }, [welcomed, ready, done, step, isRevealing]);

  // Navigate home after done
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => navigate("/"), 2000);
    return () => clearTimeout(t);
  }, [done, navigate]);

  // Agent intro pause → ask agent name
  useEffect(() => {
    if (step !== S.AGENT_INTRO) return;
    const t = setTimeout(() => {
      addLine("output", COPY.askAgentName);
      setStep(S.AGENT_NAME);
      setInput("");
    }, 1800);
    return () => clearTimeout(t);
  }, [step, addLine]);

  // Agent mode keyboard shortcuts
  useEffect(() => {
    if (step !== S.AGENT_MODE) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "1") handleModeSelect(TEMPLATES[0]);
      if (e.key === "2") handleModeSelect(TEMPLATES[1]);
      if (e.key === "3") handleModeSelect(TEMPLATES[2]);
      if (e.key === "4") handleModeSelect(TEMPLATES[3]);
      if (e.key === "Escape") {
        setStep(S.AGENT_NAME);
        setInput(agentName);
        addLine("output", COPY.askAgentName);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, agentName]);

  // Setup-only mode (already provisioned, agent not yet configured)
  useEffect(() => {
    if (!isProvisioned || !user || isLoading || done || setupOnlyStartedRef.current) return;
    setupOnlyStartedRef.current = true;
    const firstName = user.name.split(" ")[0];
    setPendingUser({ id: user.id, username: user.username, name: user.name });
    setData((d) => ({ ...d, name: firstName }));
    setWelcomed(true);
    setReady(true);
    addLine("output", COPY.agentIntro(firstName));
    setStep(S.AGENT_INTRO);
  }, [isProvisioned, user, isLoading, done, addLine, setWelcomed]);

  // ── Actions ────────────────────────────────────────────────────────────

  const advance = () => { setStep((s) => s + 1); setInput(""); };

  const goBack = useCallback(() => {
    if (step <= S.NAME || step >= S.RECOVERY || done || !ready) return;
    const prevStep = step - 1;
    const snapshot = stepSnapshots.current.get(prevStep);
    if (snapshot !== undefined) trimLines(snapshot);
    const restore: Record<number, string> = { [S.NAME]: data.name, [S.USERNAME]: data.username };
    setInput(restore[prevStep] ?? "");
    setStep(prevStep);
    stepSnapshots.current.delete(prevStep);
    stepSnapshots.current.delete(step);
  }, [step, done, ready, data, trimLines]);

  const handleModeSelect = async (template: PersonaCardData) => {
    if (!pendingUser || savingProfile) return;
    setSavingProfile(true);
    try {
      await api.consciousness.updateAgentProfile(pendingUser.id, {
        agentName: agentName.trim(),
        relationship: template.relationship,
        personaTemplate: template.persona,
      });
    } catch { /* proceed anyway */ }
    setUser(pendingUser);
    addLine("output", COPY.allSet(agentName.trim()));
    setDone(true);
    setSavingProfile(false);
  };

  const create = async () => {
    try {
      const u = await api.auth.register(data.username, data.password, data.name, "default", "Anima", "", "companion");
      setUnlockToken(u.unlockToken);
      setPendingUser({ id: u.id, username: u.username, name: u.name });
      if (u.recoveryPhrase) {
        setRecoveryPhrase(u.recoveryPhrase);
        setStep(S.RECOVERY);
        setInput("");
      } else {
        addLine("output", COPY.agentIntro(data.name));
        setStep(S.AGENT_INTRO);
        setInput("");
      }
    } catch (e) {
      addLine("error", e instanceof Error ? e.message : "Error");
    }
  };

  const submit = useCallback(() => {
    if (!input.trim() || done || isRevealing) return;
    if (!stepSnapshots.current.has(step)) stepSnapshots.current.set(step, lines.length);
    const v = input.trim();
    addLine("input", `> ${cur.password ? "*".repeat(v.length) : v}`);

    switch (step) {
      case S.NAME:
        if (v.length < 2) return addLine("error", COPY.errShort);
        setData((d) => ({ ...d, name: v }));
        addLine("output", COPY.greetUsername(v));
        advance(); break;
      case S.USERNAME:
        if (v.length < 2) return addLine("error", COPY.errShort);
        setData((d) => ({ ...d, username: v }));
        addLine("output", COPY.askPassword);
        advance(); break;
      case S.PASSWORD:
        if (v.length < 6) return addLine("error", COPY.errMinChars);
        setData((d) => ({ ...d, password: v }));
        addLine("output", COPY.confirmPwd);
        advance(); break;
      case S.VERIFY:
        if (v !== data.password) return addLine("error", COPY.errNoMatch);
        addLine("output", COPY.confirmCreate(data.name));
        advance(); break;
      case S.CONFIRM:
        if (v.toLowerCase() !== "yes") return addLine("error", COPY.errCancelled);
        addLine("output", COPY.creating);
        create(); break;
      case S.AGENT_NAME:
        if (v.length < 1) return addLine("error", COPY.errShort);
        setAgentName(v);
        addLine("output", COPY.askAgentMode(v));
        setStep(S.AGENT_MODE);
        setInput(""); break;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input, done, isRevealing, step, lines.length, data, cur, addLine]);

  const confirmCreate = useCallback(() => {
    if (step !== S.CONFIRM || done) return;
    addLine("output", COPY.creating);
    create();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, done, addLine]);

  const advanceFromRecovery = useCallback(() => {
    addLine("output", COPY.agentIntro(data.name));
    setStep(S.AGENT_INTRO);
    setInput("");
  }, [addLine, data.name]);

  const backToAgentName = useCallback(() => {
    setStep(S.AGENT_NAME);
    setInput(agentName);
    addLine("output", COPY.askAgentName);
  }, [agentName, addLine]);

  return {
    step, input, setInput, ready, done,
    recoveryPhrase, agentName, pendingUser, savingProfile,
    cur, inputRef,
    submit, goBack, confirmCreate, handleModeSelect,
    advanceFromRecovery, backToAgentName,
  };
}
