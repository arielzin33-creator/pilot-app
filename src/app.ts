import type { IncomingMessage, ServerResponse } from "node:http";
import { LinkStore } from "./store.js";
import { isValidSlug, isValidUrl } from "./validation.js";
import { paginate, parsePaging } from "./pagination.js";
import { computeStats } from "./stats.js";

export interface AppResponse {
  status: number;
  headers: Record<string, string>;
  body: string;
}

function json(status: number, value: unknown): AppResponse {
  return { status, headers: { "content-type": "application/json" }, body: JSON.stringify(value) };
}

function error(status: number, message: string): AppResponse {
  return { status, headers: {}, body: JSON.stringify({ error: message }) };
}

/** Pure request handler: method, path with query, and raw body in; response out. */
export function createApp(store: LinkStore = new LinkStore()) {
  return function handle(method: string, rawUrl: string, body = ""): AppResponse {
    const url = new URL(rawUrl, "http://localhost");
    const path = url.pathname;

    if (method === "GET" && path === "/links") {
      const { page, limit } = parsePaging(url.searchParams);
      return json(200, paginate(store.list(), page, limit));
    }

    if (method === "POST" && path === "/links") {
      const input = JSON.parse(body || "{}") as { slug?: string; target?: string; ttlSeconds?: number };
      if (!input.slug || !isValidSlug(input.slug)) return error(400, "invalid slug");
      if (!input.target || !isValidUrl(input.target)) return error(400, "invalid target URL");
      const link = store.create(input.slug, input.target, input.ttlSeconds);
      return json(201, link);
    }

    if (method === "GET" && path === "/stats") {
      return json(200, computeStats(store.list()));
    }

    const linkMatch = path.match(/^\/links\/([^/]+)$/);
    if (linkMatch && method === "DELETE") {
      const removed = store.delete(linkMatch[1] ?? "");
      return removed ? json(200, { deleted: true }) : error(404, "not found");
    }
    if (linkMatch && method === "GET") {
      const link = store.get(linkMatch[1] ?? "");
      return link ? json(200, link) : error(404, "not found");
    }

    const redirectMatch = path.match(/^\/r\/([^/]+)$/);
    if (redirectMatch && method === "GET") {
      const link = store.resolve(redirectMatch[1] ?? "");
      if (!link) return error(404, "not found");
      return { status: 302, headers: { location: link.target }, body: "" };
    }

    return error(404, "not found");
  };
}

/** Adapter from node:http to the pure handler. */
export function nodeHandler(handle: ReturnType<typeof createApp>) {
  return (req: IncomingMessage, res: ServerResponse) => {
    let body = "";
    req.on("data", (chunk: Buffer) => {
      body += chunk.toString("utf8");
    });
    req.on("end", () => {
      try {
        const out = handle(req.method ?? "GET", req.url ?? "/", body);
        res.writeHead(out.status, out.headers);
        res.end(out.body);
      } catch {
        res.writeHead(500, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: "internal error" }));
      }
    });
  };
}
