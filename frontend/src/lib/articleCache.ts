import type { Article } from "./types";

// Full Article objects, not just hashes — same reasoning as bookmarks.ts:
// there's no "get article by hash" endpoint, so /article/[hash] has no way
// to re-fetch what a card already has in hand. sessionStorage (not
// localStorage) since this is just a handoff for the click-to-navigate
// path, not something meant to outlive the tab — bookmarked articles get
// their own durable copy via lib/bookmarks.ts, which ArticleDetail also
// checks as a fallback.
const CACHE_KEY = "radar:article-cache";
const MAX_ENTRIES = 50;

function readCache(): Record<string, Article> {
  try {
    const saved = sessionStorage.getItem(CACHE_KEY);
    const parsed = saved ? JSON.parse(saved) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function stashArticle(article: Article): void {
  try {
    const cache = readCache();
    cache[article.article_hash] = article;
    const entries = Object.entries(cache);
    const trimmed =
      entries.length > MAX_ENTRIES ? entries.slice(entries.length - MAX_ENTRIES) : entries;
    sessionStorage.setItem(CACHE_KEY, JSON.stringify(Object.fromEntries(trimmed)));
  } catch {
    // Storage unavailable (private browsing, quota) — ArticleDetail's
    // bookmarks fallback / not-found state still handles this.
  }
}

export function getStashedArticle(hash: string): Article | null {
  try {
    return readCache()[hash] ?? null;
  } catch {
    return null;
  }
}
