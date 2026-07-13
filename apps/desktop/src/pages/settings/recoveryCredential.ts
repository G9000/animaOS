export type RecoveryPhraseReview = {
  phase: "review" | "complete";
  phrase: string | null;
  pendingGeneration: number | null;
  scope: "full" | "soul" | "fs" | null;
  error: string | null;
};

export function validateNewPassword(password: string): string | null {
  if (password.length < 8) {
    return "New password must be at least 8 characters.";
  }
  return null;
}

export function beginRecoveryPhraseReview(
  phrase: string,
  pendingGeneration: number,
  scope: "full" | "soul" | "fs",
): RecoveryPhraseReview {
  if (!phrase.trim() || pendingGeneration <= 0) {
    throw new Error("The replacement recovery phrase is empty.");
  }
  return { phase: "review", phrase, pendingGeneration, scope, error: null };
}

export function validateRecoveryPhraseConfirmation(
  state: RecoveryPhraseReview,
  confirmation: string,
): RecoveryPhraseReview {
  if (state.phase !== "review" || state.phrase === null) {
    return state;
  }
  if (confirmation.trim() !== state.phrase) {
    return {
      ...state,
      error: "Type the new recovery phrase exactly to confirm it.",
    };
  }
  return { ...state, error: null };
}

export function completeRecoveryPhraseReview(
  state: RecoveryPhraseReview,
): RecoveryPhraseReview {
  if (state.phase !== "review") {
    return state;
  }
  return {
    phase: "complete",
    phrase: null,
    pendingGeneration: null,
    scope: null,
    error: null,
  };
}
