import { Hono } from "hono";
import { exportEncryptedVault, importEncryptedVault } from "./handlers";

const vault = new Hono();

vault.post("/export", exportEncryptedVault);
vault.post("/import", importEncryptedVault);

export default vault;
