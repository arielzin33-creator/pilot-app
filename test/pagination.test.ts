import { describe, expect, it } from "vitest";
import { parsePaging } from "../src/pagination.js";

describe("parsePaging", () => {
  it("uses defaults when parameters are missing", () => {
    expect(parsePaging(new URLSearchParams())).toEqual({ page: 1, limit: 20 });
  });

  it("falls back on invalid values", () => {
    expect(parsePaging(new URLSearchParams("page=-3&limit=abc"))).toEqual({ page: 1, limit: 20 });
  });
});
