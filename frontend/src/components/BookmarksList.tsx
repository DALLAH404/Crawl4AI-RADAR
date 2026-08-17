"use client";

import { useSyncExternalStore } from "react";
import { BOOKMARKS_CHANGED_EVENT, getBookmarks } from "@/lib/bookmarks";
import { ArticleCard } from "./ArticleCard";

// useSyncExternalStore rather than useState+useEffect (see BookmarkButton's
// comment) — also means this list updates live the moment a card's
// BookmarkButton is toggled, no extra plumbing needed.
function subscribe(callback: () => void) {
  window.addEventListener(BOOKMARKS_CHANGED_EVENT, callback);
  return () => window.removeEventListener(BOOKMARKS_CHANGED_EVENT, callback);
}

const EMPTY: ReturnType<typeof getBookmarks> = [];

export function BookmarksList() {
  const bookmarks = useSyncExternalStore(subscribe, getBookmarks, () => EMPTY);

  if (bookmarks.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border py-24 text-center">
        <svg viewBox="0 0 24 24" className="size-10 text-muted-foreground" aria-hidden="true">
          <path
            d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z"
            fill="none"
            stroke="currentColor"
            strokeOpacity="0.4"
            strokeWidth="1.5"
            strokeLinejoin="round"
          />
        </svg>
        <p className="font-medium text-foreground">No bookmarks yet</p>
        <p className="max-w-sm text-sm text-muted-foreground">
          Save an article from any card and it&apos;ll show up here.
        </p>
      </div>
    );
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {bookmarks.map((article) => (
        <ArticleCard key={article.article_hash} article={article} />
      ))}
    </div>
  );
}
