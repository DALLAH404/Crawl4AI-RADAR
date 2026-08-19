import type { Article } from "./types";

// Per-article bookmarks (distinct from CompanyFilter's per-company
// watchlist) — stores full Article objects, not just hashes, since there's
// no "get article by hash" endpoint to re-fetch them from for the
// /bookmarks page. Client-only feature, no backend involved.
const BOOKMARKS_KEY = "radar:bookmarks";

// The native "storage" event only fires in *other* tabs, never the one that
// made the change — this custom event is what lets BookmarksList stay in
// sync when a card's BookmarkButton is toggled on the same page (e.g.
// un-bookmarking directly from /bookmarks).
export const BOOKMARKS_CHANGED_EVENT = "radar:bookmarks-changed";

function readBookmarks(): Article[] {
  try {
    const saved = localStorage.getItem(BOOKMARKS_KEY);
    const parsed = saved ? JSON.parse(saved) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

// useSyncExternalStore requires getSnapshot to return a *stable* reference
// when nothing's changed — readBookmarks() parses fresh JSON every call, so
// a new array each time looks like a change on every render and loops
// forever. This caches the array and only recomputes right after a real
// write (toggleBookmark), not on every render that happens to call it.
let cache: Article[] | null = null;

export function getBookmarks(): Article[] {
  if (cache === null) {
    cache = readBookmarks();
  }
  return cache;
}

export function isBookmarked(articleHash: string): boolean {
  return getBookmarks().some((a) => a.article_hash === articleHash);
}

export function toggleBookmark(article: Article): boolean {
  const current = getBookmarks();
  const exists = current.some((a) => a.article_hash === article.article_hash);
  const next = exists
    ? current.filter((a) => a.article_hash !== article.article_hash)
    : [article, ...current];
  localStorage.setItem(BOOKMARKS_KEY, JSON.stringify(next));
  cache = next;
  window.dispatchEvent(new Event(BOOKMARKS_CHANGED_EVENT));
  return !exists;
}

export function clearBookmarks(): void {
  localStorage.removeItem(BOOKMARKS_KEY);
  cache = [];
  window.dispatchEvent(new Event(BOOKMARKS_CHANGED_EVENT));
}
