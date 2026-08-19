"use client";

import { useSyncExternalStore } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { Article } from "@/lib/types";
import { getBookmarks } from "@/lib/bookmarks";
import { getStashedArticle } from "@/lib/articleCache";
import { cleanScrapedText } from "@/lib/cleanScrapedText";
import { AlertBadge } from "./AlertBadge";
import { BookmarkButton } from "./BookmarkButton";
import { CompanyBadge } from "./CompanyBadge";
import { CompanyLogoFallback } from "./CompanyLogoFallback";
import { RelativeTime } from "./RelativeTime";

function subscribe() {
  return () => {};
}

// Same mount-gating trick as ThemeToggle/RelativeTime — the lookup below
// only has anything to find in the browser (sessionStorage/localStorage),
// so the server-rendered pass and the client's first paint must agree on
// "nothing yet" to avoid a hydration mismatch.
function useHasMounted() {
  return useSyncExternalStore(subscribe, () => true, () => false);
}

// Full-page counterpart to the old ArticleModal, at /article/[hash]. There's
// no "get article by hash" API (see articleCache.ts), so the article a card
// was clicked with is stashed client-side and looked up here — falling back
// to a bookmarked copy (durable across sessions) for direct/refreshed visits
// to an article that happens to be saved, and a not-found state otherwise.
export function ArticleDetail({ hash }: { hash: string }) {
  const router = useRouter();
  const mounted = useHasMounted();

  if (!mounted) {
    return (
      <div className="animate-pulse">
        <div className="aspect-[16/9] w-full rounded-xl bg-muted" />
        <div className="mt-4 h-6 w-2/3 rounded bg-muted" />
        <div className="mt-3 h-4 w-full rounded bg-muted" />
      </div>
    );
  }

  const article: Article | null =
    getStashedArticle(hash) ?? getBookmarks().find((a) => a.article_hash === hash) ?? null;

  if (!article) {
    return (
      <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border py-24 text-center">
        <p className="font-medium text-foreground">Article not found</p>
        <p className="max-w-sm text-sm text-muted-foreground">
          This link didn&apos;t come with the article&apos;s data, and it isn&apos;t bookmarked
          either — go back and open it from a card instead.
        </p>
        <Link
          href="/"
          className="mt-2 rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-accent hover:text-accent-foreground"
        >
          ← Back to feed
        </Link>
      </div>
    );
  }

  // No raw title on the page — the scraped title/post are markdown
  // straight off LinkedIn (mentions and hashtags as literal [text](url)
  // links, tracking params and all), not prose meant for display.
  // summary (AI-written) is always preferred; title/post only stand in
  // when there's no summary, and only after stripping that markdown junk
  // out — see cleanScrapedText.
  const displayText =
    article.summary || cleanScrapedText(article.title) || cleanScrapedText(article.action_description);

  // article.image_urls may be missing on a bookmark saved before this field
  // existed — bookmarks are durable raw JSON in localStorage (lib/bookmarks.ts),
  // not re-fetched from the API, so old entries keep whatever shape they were
  // saved with.
  const images = article.image_urls?.length
    ? article.image_urls
    : article.image_url
      ? [article.image_url]
      : [];

  return (
    <article className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => router.back()}
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Back to feed
        </button>
        <BookmarkButton article={article} />
      </div>

      {images.length > 0 ? (
        <div className="flex flex-col gap-2">
          {images.map((url) => (
            <div key={url} className="aspect-[16/9] w-full overflow-hidden rounded-xl bg-muted">
              {/* eslint-disable-next-line @next/next/no-img-element -- external, unpredictable source hosts */}
              <img src={url} alt="" className="size-full object-contain" />
            </div>
          ))}
        </div>
      ) : (
        article.companies[0] && (
          <div className="aspect-[16/9] w-full overflow-hidden rounded-xl bg-muted">
            <CompanyLogoFallback company={article.companies[0]} />
          </div>
        )
      )}

      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span><RelativeTime publishedAt={article.published_at} /></span>
        {article.source_name && <span>· {article.source_name}</span>}
        {article.content_kind === "social" && (
          <span className="rounded-full bg-muted px-2 py-0.5 font-medium text-muted-foreground">
            Social
          </span>
        )}
        <AlertBadge level={article.alert_level} />
      </div>

      {displayText && (
        <p className="text-base leading-relaxed text-muted-foreground">{displayText}</p>
      )}

      {article.competitor_analysis && (
        <div className="rounded-lg border border-border bg-muted/50 p-3">
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Remark
          </p>
          <p className="text-sm leading-relaxed text-foreground">{article.competitor_analysis}</p>
        </div>
      )}

      {article.companies.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {article.companies.map((name) => (
            <CompanyBadge key={name} name={name} href={`/company/${encodeURIComponent(name)}`} />
          ))}
        </div>
      )}

      <a
        href={article.link}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-2 inline-flex w-fit items-center justify-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
      >
        Read full {article.content_kind === "social" ? "post" : "article"} at the source
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-3.5" aria-hidden="true">
          <path d="M7 17L17 7M7 7h10v10" />
        </svg>
      </a>
    </article>
  );
}
