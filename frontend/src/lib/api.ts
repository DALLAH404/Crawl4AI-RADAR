import type { ArticlesResponse, ContentKind } from "./types";

// Server-only — no NEXT_PUBLIC_ prefix. Every fetch against the read API
// happens in a Server Component (see app/page.tsx, app/company/[name]/page.tsx),
// so the base URL never needs to reach the browser bundle.
const API_BASE_URL = process.env.RADAR_API_BASE_URL;

// "Revalidate every few minutes" per the brief — the scraper itself only
// runs every 3 hours, so this is already far more frequent than the data
// actually changes; it's chosen for a snappy-feeling site, not because the
// source refreshes this often.
const REVALIDATE_SECONDS = 180;

export interface GetArticlesParams {
  company?: string[];
  kind?: ContentKind;
  from?: string;
  to?: string;
  limit?: number;
  cursor?: string;
}

export async function getArticles(
  params: GetArticlesParams = {},
): Promise<ArticlesResponse> {
  if (!API_BASE_URL) {
    throw new Error(
      "RADAR_API_BASE_URL is not set — see .env.example and set it in .env.local",
    );
  }

  const query = new URLSearchParams();
  if (params.company?.length) query.set("company", params.company.join(","));
  if (params.kind) query.set("kind", params.kind);
  if (params.from) query.set("from", params.from);
  if (params.to) query.set("to", params.to);
  if (params.limit) query.set("limit", String(params.limit));
  if (params.cursor) query.set("cursor", params.cursor);

  const url = `${API_BASE_URL}/articles${query.size ? `?${query}` : ""}`;

  const res = await fetch(url, {
    next: { revalidate: REVALIDATE_SECONDS },
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(
      `RADAR API request failed (${res.status} ${res.statusText}): ${url}\n${body}`,
    );
  }

  return res.json() as Promise<ArticlesResponse>;
}
