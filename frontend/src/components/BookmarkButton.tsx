"use client";

import { useSyncExternalStore } from "react";
import type { Article } from "@/lib/types";
import { BOOKMARKS_CHANGED_EVENT, isBookmarked, toggleBookmark } from "@/lib/bookmarks";

// useSyncExternalStore rather than useState+useEffect (which trips this
// repo's react-hooks/set-state-in-effect rule, see ThemeToggle/RelativeTime)
// — bonus here is it also picks up toggleBookmark's change event
// automatically, so this stays correct even if the same article's bookmark
// state changes elsewhere on the page without any extra wiring.
function subscribe(callback: () => void) {
  window.addEventListener(BOOKMARKS_CHANGED_EVENT, callback);
  return () => window.removeEventListener(BOOKMARKS_CHANGED_EVENT, callback);
}

// Sits inside a clickable card (see ArticleCard), so its own click must not
// also trigger the card's onClick (navigating to the article page) —
// stopPropagation, same pattern as the company badges.
export function BookmarkButton({ article, size = "md" }: { article: Article; size?: "sm" | "md" }) {
  const saved = useSyncExternalStore(
    subscribe,
    () => isBookmarked(article.article_hash),
    () => false, // server snapshot — SSR has no localStorage, so "not saved"
  );

  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        toggleBookmark(article);
      }}
      aria-label={saved ? "Remove bookmark" : "Save bookmark"}
      aria-pressed={saved}
      className={`flex shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-accent-foreground ${size === "sm" ? "size-6" : "size-7"}`}
    >
      <svg
        viewBox="0 0 24 24"
        fill={saved ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="2"
        className={`${size === "sm" ? "size-3.5" : "size-4"} ${saved ? "text-primary" : ""}`}
        aria-hidden="true"
      >
        <path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z" strokeLinejoin="round" />
      </svg>
    </button>
  );
}
