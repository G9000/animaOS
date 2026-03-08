// Auth routes — register and login

import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { registerSchema, loginSchema } from "./schema";
import { register, login, logout, me, localBootstrap } from "./handlers";

const auth = new Hono();

auth.post("/register", zValidator("json", registerSchema), register);
auth.post("/login", zValidator("json", loginSchema), login);
auth.get("/me", me);
auth.post("/logout", logout);
auth.post("/local/bootstrap", localBootstrap);

export default auth;
