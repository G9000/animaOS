import type { PersonaCardData } from "../../components/PersonaTemplateCards";

export interface Line {
  id: number;
  type: "output" | "input" | "error";
  text: string;
  revealed: string;
}

export interface StepDef {
  label: string;
  placeholder: string;
  password?: boolean;
}

export const STEPS: StepDef[] = [
  { label: "name",        placeholder: "e.g. Alice" },
  { label: "username",    placeholder: "lowercase, no spaces" },
  { label: "password",    placeholder: "at least 6 characters", password: true },
  { label: "verify",      placeholder: "re-enter password",     password: true },
  { label: "confirm",     placeholder: "yes or no" },
  { label: "recovery",    placeholder: "" },
  { label: "agent_intro", placeholder: "" },
  { label: "agent_name",  placeholder: "e.g. Anima" },
  { label: "agent_mode",  placeholder: "" },
];

export const S = {
  NAME:        0,
  USERNAME:    1,
  PASSWORD:    2,
  VERIFY:      3,
  CONFIRM:     4,
  RECOVERY:    5,
  AGENT_INTRO: 6,
  AGENT_NAME:  7,
  AGENT_MODE:  8,
} as const;

export const TEMPLATES: PersonaCardData[] = [
  {
    id: "blank",
    label: "Blank Slate",
    desc: "No preset personality. Everything shaped through conversation.",
    relationship: "",
    persona: "default",
  },
  {
    id: "companion",
    label: "Companion",
    desc: "Warm and attuned. Listens closely, responds with genuine care.",
    relationship: "companion",
    persona: "companion",
  },
  {
    id: "anima",
    label: "Anima",
    desc: "Quiet and reflective. Understands deeply, challenges gently.",
    relationship: "companion",
    persona: "anima",
  },
];

export const GREETINGS = [
  "hello",    // en
  "hola",     // es
  "bonjour",  // fr
  "hallo",    // de
  "ciao",     // it
  "привет",   // ru
  "merhaba",  // tr
  "namaste",  // hi
  "こんにちは",  // ja
  "안녕",      // ko
  "你好",      // zh
  "sawubona", // zu
  "مرحبا",    // ar
];

export const COPY = {
  askName:       "What should I call you?",
  greetUsername: (name: string) => `Hey ${name}. Pick a username.`,
  askPassword:   "Good. Now a password — at least 6 characters.",
  confirmPwd:    "One more time — just to be sure.",
  confirmCreate: (name: string) => `Ready, ${name}?`,
  creating:      "Sealing the vault.",
  recoveryLabel: "Before you go in.",
  recoverySub:   "Write these 12 words down. They're the only way back if you lose your password.",
  agentIntro:    (name: string) => `One more thing, ${name}.`,
  askAgentName:  "What should I call myself?",
  askAgentMode:  (n: string) => `How should ${n} begin?`,
  allSet:        (n: string) => `${n} is ready.`,
  errShort:      "Too short",
  errMinChars:   "Min 6 chars",
  errNoMatch:    "Doesn't match. Try again.",
  errCancelled:  "Cancelled",
};
