import { useState } from "react";

export type WaitlistState = "idle" | "loading" | "done";

export function useWaitlist() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<WaitlistState>("idle");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || state !== "idle") return;
    setState("loading");

    const res = await fetch(
      `https://api.airtable.com/v0/${import.meta.env.PUBLIC_AIRTABLE_BASE_ID}/${import.meta.env.PUBLIC_AIRTABLE_TABLE_NAME}`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${import.meta.env.PUBLIC_AIRTABLE_TOKEN}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ records: [{ fields: { "Email Address": email } }] }),
      }
    );

    if (res.ok) {
      (window as any).umami?.track("waitlist-signup");
      setState("done");
    } else {
      setState("idle");
    }
  }

  return { email, setEmail, state, submit };
}
