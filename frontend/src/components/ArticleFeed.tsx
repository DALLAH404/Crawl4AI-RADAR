"use client";

import { useState } from "react";
import type { Article, ArticlesResponse, ContentKind } from "@/lib/types";
import { ArticleCard } from "./ArticleCard";

// The homepage's main feed. Takes over from page.tsx's server-rendered first
// page and appends subsequent ones in place on "Load more" — a normal <Link>
// to ?cursor=... would instead navigate to a page showing only that page's
// items, replacing everything already on screen. Fetches through
// /api/articles (a thin server-only proxy — see that route) since the read
// API's base URL is a server-only env var, unreachable directly from here.
export function ArticleFeed({
  initialItems,
  initialCursor,
  kind,
}: {
  initialItems: Article[];
  initialCursor: string | null;
  kind: ContentKind | undefined;
}) {
  const [items, setItems] = useState(initialItems);
  const [cursor, setCursor] = useState(initialCursor);
  const [loading, setLoading] = useState(false);

  async function loadMore() {
    if (!cursor || loading) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({ cursor });
      if (kind) params.set("kind", kind);
      const res = await fetch(`/api/articles?${params}`);
      if (!res.ok) return;
      const data: ArticlesResponse = await res.json();
      setItems((prev) => {
        const seen = new Set(prev.map((a) => a.article_hash));
        return [...prev, ...data.items.filter((a) => !seen.has(a.article_hash))];
      });
      setCursor(data.next_cursor);
    } finally {
      setLoading(false);
    }
  }

  if (items.length === 0) {
    return <EmptyState />;
  }

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        {items.map((article) => (
          <ArticleCard key={article.article_hash} article={article} />
        ))}
      </div>

      {cursor && (
        <button
          type="button"
          onClick={loadMore}
          disabled={loading}
          className="mx-auto mt-4 block w-fit rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
        >
          {loading ? "Loading…" : "Load more"}
        </button>
      )}
    </>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border py-24 text-center">
      <svg viewBox="0 0 24 24" className="size-10 text-muted-foreground" aria-hidden="true">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.4" strokeWidth="1.5" />
        <circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" strokeOpacity="0.4" strokeWidth="1.5" />
      </svg>
      <p className="font-medium text-foreground">Nothing on the radar yet</p>
      <p className="max-w-sm text-sm text-muted-foreground">
        No articles match this filter. Try clearing it or checking back after the next scan.
      </p>
    </div>
  );
}
