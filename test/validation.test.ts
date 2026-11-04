import { describe, expect, it } from "vitest";
import { isValidSlug, isValidUrl } from "../src/validation.js";

describe("validation", () => {
  it("accepts a simple slug", () => {
    expect(isValidSlug("my-link-1")).toBe(true);
  });

  it("rejects slugs with spaces", () => {
    expect(isValidSlug("my link")).toBe(false);
  });

  it("accepts https URLs", () => {
    expect(isValidUrl("https://example.com/path?q=1")).toBe(true);
  });

  it("rejects text that is not a URL", () => {
    expect(isValidUrl("not a url")).toBe(false);
  });
});
