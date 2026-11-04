import type { Link } from "./types.js";

/** In-memory link store. A real deployment replaces this with PostgreSQL (Phase D). */
export class LinkStore {
  private readonly links = new Map<string, Link>();

  constructor(private readonly now: () => number = Date.now) {}

  create(slug: string, target: string, ttlSeconds?: number): Link {
    if (this.links.has(slug)) {
      throw new Error(`slug already exists: ${slug}`);
    }
    const createdAt = this.now();
    const link: Link = {
      slug,
      target,
      createdAt,
      expiresAt: ttlSeconds ? createdAt + ttlSeconds * 1000 : null,
      clicks: 0,
    };
    this.links.set(slug, link);
    return link;
  }

  get(slug: string): Link | undefined {
    return this.links.get(slug);
  }

  /** Resolve a slug for redirecting. */
  resolve(slug: string): Link | undefined {
    return this.links.get(slug);
  }

  delete(slug: string): boolean {
    return this.links.delete(slug);
  }

  /** All links, newest first. */
  list(): Link[] {
    return [...this.links.values()];
  }
}
