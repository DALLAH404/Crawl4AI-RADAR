import { NextRequest } from "next/server";
import { getArticles } from "@/lib/api";
import type { ContentKind } from "@/lib/types";

function parseKind(value: string | null): ContentKind | undefined {
  return value === "news" || value === "social" ? value : undefined;
}

// Server-only proxy so client-side pagination (ArticleFeed.tsx's "Load
// more") can fetch subsequent pages without exposing RADAR_API_BASE_URL to
// the browser — same reasoning as /api/stocks for TWELVE_DATA_API_KEY.
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const company = searchParams.get("company");
  const limitParam = searchParams.get("limit");

  const result = await getArticles({
    company: company ? company.split(",").filter(Boolean) : undefined,
    kind: parseKind(searchParams.get("kind")),
    from: searchParams.get("from") ?? undefined,
    to: searchParams.get("to") ?? undefined,
    cursor: searchParams.get("cursor") ?? undefined,
    limit: limitParam ? Number(limitParam) : undefined,
  });

  return Response.json(result);
}
