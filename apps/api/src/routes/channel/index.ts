import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { sendChannelMessage } from "./handlers";
import { channelMessageSchema } from "./schema";

const channel = new Hono();

channel.post("/message", zValidator("json", channelMessageSchema), sendChannelMessage);

export default channel;
