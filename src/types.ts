export interface Link {
  slug: string;
  target: string;
  createdAt: number;
  expiresAt: number | null;
  clicks: number;
}

export interface Page<T> {
  items: T[];
  page: number;
  limit: number;
  total: number;
}
