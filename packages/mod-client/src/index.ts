import { treaty } from "@elysiajs/eden";
import type { App } from "anima-mod";

export function createModClient(baseUrl: string) {
  return treaty<App>(baseUrl);
}

export type ModClient = ReturnType<typeof createModClient>;
