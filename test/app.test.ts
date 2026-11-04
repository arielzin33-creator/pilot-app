import { describe, expect, it } from "vitest";
import { createApp } from "../src/app.js";
import { LinkStore } from "../src/store.js";

function setup() {
  const store = new LinkStore(() => 1_000_000);
  return { store, handle: createApp(store) };
}

describe("links API", () => {
  it("creates a link", () => {
    const { handle } = setup();
    const res = handle("POST", "/links", JSON.stringify({ slug: "docs", target: "https://example.com/docs" }));
    expect(res.status).toBe(201);
    expect(JSON.parse(res.body)).toMatchObject({ slug: "docs", target: "https://example.com/docs", clicks: 0 });
  });

  it("rejects a missing target", () => {
    const { handle } = setup();
    const res = handle("POST", "/links", JSON.stringify({ slug: "docs" }));
    expect(res.status).toBe(400);
  });

  it("returns a link by slug", () => {
    const { handle } = setup();
    handle("POST", "/links", JSON.stringify({ slug: "home", target: "https://example.com" }));
    const res = handle("GET", "/links/home");
    expect(res.status).toBe(200);
    expect(JSON.parse(res.body).target).toBe("https://example.com");
  });

  it("redirects a known slug", () => {
    const { handle } = setup();
    handle("POST", "/links", JSON.stringify({ slug: "home", target: "https://example.com" }));
    const res = handle("GET", "/r/home");
    expect(res.status).toBe(302);
    expect(res.headers.location).toBe("https://example.com");
  });

  it("returns 404 for an unknown slug", () => {
    const { handle } = setup();
    expect(handle("GET", "/r/nope").status).toBe(404);
  });

  it("lists links with paging metadata", () => {
    const { handle } = setup();
    handle("POST", "/links", JSON.stringify({ slug: "one", target: "https://example.com/1" }));
    const res = handle("GET", "/links");
    const body = JSON.parse(res.body);
    expect(res.status).toBe(200);
    expect(body.page).toBe(1);
    expect(body.total).toBe(1);
  });

  it("deletes a link", () => {
    const { handle } = setup();
    handle("POST", "/links", JSON.stringify({ slug: "gone", target: "https://example.com" }));
    expect(handle("DELETE", "/links/gone").status).toBeLessThan(300);
    expect(handle("GET", "/links/gone").status).toBe(404);
  });
});
