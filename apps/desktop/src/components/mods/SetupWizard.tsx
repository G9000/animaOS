import { useState } from "react";
import type { ModConfigSchema, SetupStep } from "./types";
import StatusBadge from "./StatusBadge";

interface SetupWizardProps {
  steps: SetupStep[];
  schema: ModConfigSchema;
  modId: string;
  onComplete: (config: Record<string, unknown>) => Promise<void>;
  onHealthCheck: () => Promise<boolean>;
}

export default function SetupWizard({ steps, schema, modId, onComplete, onHealthCheck }: SetupWizardProps) {
  const [currentStep, setCurrentStep] = useState(0);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [healthStatus, setHealthStatus] = useState<"idle" | "checking" | "ok" | "fail">("idle");

  const set = (key: string, val: unknown) => setValues((prev) => ({ ...prev, [key]: val }));

  const handleNext = async () => {
    const step = steps[currentStep];

    if (step.action === "healthcheck") {
      setHealthStatus("checking");
      // Save config first, then check health
      await onComplete(values);
      const ok = await onHealthCheck();
      setHealthStatus(ok ? "ok" : "fail");
      if (!ok) return;
    }

    if (currentStep < steps.length - 1) {
      setCurrentStep((s) => s + 1);
    } else {
      await onComplete(values);
    }
  };

  return (
    <div className="space-y-0">
      {steps.map((step, i) => {
        const isDone = i < currentStep;
        const isActive = i === currentStep;
        const isPending = i > currentStep;
        const field = step.field ? schema[step.field] : null;

        return (
          <div
            key={step.step}
            className={`flex gap-3 ${isPending ? "opacity-30" : ""}`}
          >
            {/* Step indicator */}
            <div className="flex flex-col items-center">
              <div
                className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-mono ${
                  isDone
                    ? "bg-success text-black"
                    : isActive
                    ? "border-2 border-primary text-primary"
                    : "border border-border text-text-muted/40"
                }`}
              >
                {isDone ? "\u2713" : step.step}
              </div>
              {i < steps.length - 1 && (
                <div className="w-px h-full min-h-[20px] bg-border/30 my-1" />
              )}
            </div>

            {/* Step content */}
            <div className={`flex-1 pb-6 ${isDone ? "opacity-50" : ""}`}>
              <div className="font-mono text-[9px] tracking-widest text-text-muted/60 uppercase mb-1">
                STEP {step.step} — {step.title}
              </div>

              {isActive && (
                <div className="mt-2 space-y-3">
                  {step.instructions && (
                    <p className="font-mono text-[10px] text-text-muted/50 leading-relaxed">
                      {step.instructions}
                    </p>
                  )}

                  {field && (
                    <div>
                      {field.type === "secret" ? (
                        <input
                          type="password"
                          placeholder={field.label}
                          value={String(values[step.field!] ?? "")}
                          onChange={(e) => set(step.field!, e.target.value)}
                          className="w-full bg-bg-input border border-border px-2 py-1.5 font-mono text-[10px] text-text focus:border-primary/50 outline-none"
                        />
                      ) : field.type === "enum" ? (
                        <div className="flex gap-1">
                          {field.options?.map((opt) => (
                            <button
                              key={opt}
                              onClick={() => set(step.field!, opt)}
                              className={`font-mono text-[9px] px-2 py-1 border transition-colors ${
                                values[step.field!] === opt
                                  ? "border-primary text-primary"
                                  : "border-border text-text-muted/40 hover:text-text"
                              }`}
                            >
                              {opt.toUpperCase()}
                            </button>
                          ))}
                        </div>
                      ) : (
                        <input
                          type="text"
                          placeholder={field.label}
                          value={String(values[step.field!] ?? "")}
                          onChange={(e) => set(step.field!, e.target.value)}
                          className="w-full bg-bg-input border border-border px-2 py-1.5 font-mono text-[10px] text-text focus:border-primary/50 outline-none"
                        />
                      )}
                    </div>
                  )}

                  {step.action === "healthcheck" && (
                    <div className="flex items-center gap-2">
                      <StatusBadge status={
                        healthStatus === "ok" ? "running" :
                        healthStatus === "fail" ? "error" :
                        healthStatus === "checking" ? "checking" : "stopped"
                      } />
                      {healthStatus === "fail" && (
                        <span className="font-mono text-[8px] text-danger">
                          Connection failed. Check your token.
                        </span>
                      )}
                    </div>
                  )}

                  <button
                    onClick={handleNext}
                    className="font-mono text-[9px] tracking-wider text-primary border border-primary/30 px-3 py-1 hover:bg-primary/10 transition-colors"
                  >
                    {i === steps.length - 1 ? "FINISH" : "NEXT"}
                  </button>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
