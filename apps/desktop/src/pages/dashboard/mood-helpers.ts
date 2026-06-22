export const MOOD_EMOJI: Record<string, string> = {
  calm: "🌿",
  happy: "✨",
  excited: "⚡",
  curious: "🔍",
  anxious: "🌊",
  sad: "🌧",
  angry: "🔥",
  frustrated: "💢",
  hopeful: "🌅",
  lonely: "🌑",
  content: "☕",
  tired: "🌫",
  playful: "🎈",
  worried: "🌩",
  grateful: "🙏",
  confused: "🌀",
  protective: "🛡",
  affectionate: "💗",
};

export const MOOD_CHAT_PROMPT: Record<string, string> = {
  calm: "What's keeping you so grounded right now?",
  happy: "What's bringing you that happiness?",
  excited: "What's got you so excited?",
  curious: "What are you most curious about today?",
  anxious: "What's been weighing on your mind?",
  sad: "Want to talk about how you're feeling?",
  angry: "What's got you fired up?",
  frustrated: "What's been frustrating you lately?",
  hopeful: "What are you feeling hopeful about?",
  lonely: "I'm here. What's making you feel distant?",
  content: "What's bringing you that sense of peace?",
  tired: "What's been draining your energy?",
  playful: "What do you want to play with today?",
  worried: "What's been weighing on you?",
  grateful: "What are you feeling grateful for?",
  confused: "What's got you feeling uncertain?",
  protective: "What do you feel protective of?",
  affectionate: "Who or what are you feeling close to right now?",
};

export function getMoodEmoji(emotion: string | null): string {
  if (!emotion) return "◌";
  return MOOD_EMOJI[emotion.toLowerCase().trim()] ?? "◌";
}

export function getMoodChatPrompt(emotion: string): string {
  return (
    MOOD_CHAT_PROMPT[emotion.toLowerCase().trim()] ??
    `Tell me more about feeling ${emotion}.`
  );
}
