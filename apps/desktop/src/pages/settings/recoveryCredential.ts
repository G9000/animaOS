export type RecoveryPhraseReview = {
  phase: "review" | "complete";
  phrase: string | null;
  pendingGeneration: number | null;
  scope: "full" | "soul" | "fs" | null;
  currentPassword: string | null;
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
  currentPassword: string,
): RecoveryPhraseReview {
  if (!phrase.trim() || pendingGeneration <= 0 || !currentPassword) {
    throw new Error("The replacement recovery phrase and current password are required.");
  }
  return {
    phase: "review",
    phrase,
    pendingGeneration,
    scope,
    currentPassword,
    error: null,
  };
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
    currentPassword: null,
    error: null,
  };
}
