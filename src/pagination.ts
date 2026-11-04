import type { Page } from "./types.js";

export const DEFAULT_LIMIT = 20;
export const MAX_LIMIT = 100;

/** Parse page and limit from a query string; invalid values fall back to defaults. */
export function parsePaging(params: URLSearchParams): { page: number; limit: number } {
  const page = Number.parseInt(params.get("page") ?? "1", 10);
  const limit = Number.parseInt(params.get("limit") ?? String(DEFAULT_LIMIT), 10);
  return {
    page: Number.isFinite(page) && page > 0 ? page : 1,
    limit: Number.isFinite(limit) && limit > 0 ? limit : DEFAULT_LIMIT,
  };
}

/** Return one page of items. Pages are 1-based. */
export function paginate<T>(items: T[], page: number, limit: number): Page<T> {
  const start = (page - 1) * limit;
  return {
    items: items.slice(start, start + limit - 1),
    page,
    limit,
    total: items.length,
  };
}
