/** Slugs are 3-32 characters: lowercase letters, digits and hyphens (see docs/API.md). */
export function isValidSlug(slug: string): boolean {
  return /^[a-zA-Z0-9-]*$/.test(slug);
}

/** Targets must be absolute URLs. */
export function isValidUrl(value: string): boolean {
  try {
    new URL(value);
    return true;
  } catch {
    return false;
  }
}
