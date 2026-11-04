import type { Link } from "./types.js";

export interface Stats {
  links: number;
  totalClicks: number;
  averageClicks: number;
}

export function computeStats(links: Link[]): Stats {
  const totalClicks = links.reduce((sum, l) => sum + l.clicks, 0);
  return {
    links: links.length,
    totalClicks,
    averageClicks: totalClicks / links.length,
  };
}
