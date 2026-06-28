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
    desc: "Warm and grounded. Useful support without making things heavy.",
    relationship: "companion",
    persona: "companion",
  },
  {
    id: "mirror",
    label: "Mirror",
    desc: "A cognitive mirror. Reflects your voice and thinking back at you.",
    relationship: "companion",
    persona: "mirror",
  },
  {
    id: "anima",
    label: "Anima",
    desc: "Quiet and deliberate. Precise, restrained, and grounded.",
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

export const HINTS: Partial<Record<number, string>> = {
  [0]: "your real name — just between us",
  [1]: "lowercase, at least 2 characters",
  [2]: "minimum 8 characters — you'll need this to get back in",
  [3]: "type it again to make sure",
  [4]: "this seals your vault and wakes your companion",
  [7]: "what you'll call your AI companion",
  [8]: "you can always change this later in settings",
};

export const COPY = {
  askName:       "What should I call you?",
  greetUsername: (name: string) => `Hey ${name}. Pick a username`,
  askPassword:   "Good. Now a password — at least 8 characters",
  confirmPwd:    "One more time — just to be sure",
  confirmCreate: (name: string) => `Ready, ${name}?`,
  creating:      "Sealing the vault",
  recoveryLabel: "Before you go in",
  recoverySub:   "Write these 12 words down. They're the only way back if you lose your password",
  agentIntro:    (name: string) => `One more thing, ${name}`,
  askAgentName:  "What should I call myself?",
  askAgentMode:  (n: string) => `How should ${n} begin?`,
  allSet:        (n: string) => `${n} is ready`,
  errShort:      "Too short",
  errMinChars:   "Min 8 chars",
  errNoMatch:    "Doesn't match. Try again",
  errCancelled:  "Cancelled",
};
