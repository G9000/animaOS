// Soul routes — read and update ANIMA's soul definition

import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { updateSoulSchema } from "./schema";
import { getSoul, updateSoul } from "./handlers";

const soul = new Hono();

soul.get("/:userId", getSoul);
soul.put("/:userId", zValidator("json", updateSoulSchema), updateSoul);

export default soul;
