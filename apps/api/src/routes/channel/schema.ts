import { z } from "zod";

export const channelMessageSchema = z.object({
  userId: z.number(),
  message: z.string().min(1),
});
