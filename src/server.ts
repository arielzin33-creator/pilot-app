import { createServer } from "node:http";
import { createApp, nodeHandler } from "./app.js";

const port = Number(process.env.PORT ?? 8080);
createServer(nodeHandler(createApp())).listen(port, () => {
  console.log(`pilot-links listening on http://localhost:${port}`);
});
