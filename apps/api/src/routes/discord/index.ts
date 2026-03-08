import { Hono } from "hono";
import { webhook } from "./handlers";

const discord = new Hono();

discord.post("/webhook", webhook);

export default discord;
