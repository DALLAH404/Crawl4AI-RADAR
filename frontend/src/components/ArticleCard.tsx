"use client";

import { useRouter } from "next/navigation";
import type { Article } from "@/lib/types";
import { companyColor } from "@/lib/companyColor";
import { stashArticle } from "@/lib/articleCache";
import { cleanScrapedText } from "@/lib/cleanScrapedText";
import { CompanyBadge } from "./CompanyBadge";
import { BookmarkButton } from "./BookmarkButton";
import { NewBadge } from "./NewBadge";
import { CompanyLogoFallback } from "./CompanyLogoFallback";
import { RelativeTime } from "./RelativeTime";

export function ArticleCard({ article }: { article: Article }) {
  const router = useRouter();
  const primaryCompany = article.companies[0];
  const strip = primaryCompany ? companyColor(primaryCompany) : null;

  function openArticle() {
    stashArticle(article);
    router.push(`/article/${encodeURIComponent(article.article_hash)}`);
  }

  return (
    <article
      onClick={openArticle}
      className="group flex cursor-pointer flex-col overflow-hidden rounded-md bg-card shadow-lg shadow-gray-500/20 transition-[transform,box-shadow] hover:scale-[1.03] hover:shadow-2xl hover:shadow-gray-500/30"
    >
      <div className="relative block aspect-[2/1] overflow-hidden bg-muted">
        {article.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- external, unpredictable source hosts
          <img
            src={article.image_url}
            alt=""
            className="size-full object-cover transition-transform group-hover:scale-105"
            loading="lazy"
          />
        ) : article.companies[0] ? (
          <CompanyLogoFallback company={article.companies[0]} />
        ) : (
          <div className="flex size-full items-center justify-center text-muted-foreground">
            <RadarPlaceholder />
          </div>
        )}
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-teal-500/25 via-transparent to-orange-500/25 mix-blend-overlay" />
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/20 to-transparent" />
        <div className="absolute right-1 top-1 rounded-full bg-background/80 backdrop-blur-sm">
          <BookmarkButton article={article} size="sm" />
        </div>
        {strip && (
          <>
            <div className="absolute inset-x-0 bottom-0 h-1 dark:hidden" style={{ backgroundColor: strip.light }} aria-hidden="true" />
            <div className="absolute inset-x-0 bottom-0 hidden h-1 dark:block" style={{ backgroundColor: strip.dark }} aria-hidden="true" />
          </>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-1.5 p-2.5">
        <div className="flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
          <span><RelativeTime publishedAt={article.published_at} /></span>
          {article.source_name && <span>· {article.source_name}</span>}
          {article.content_kind === "social" && (
            <span className="rounded-full bg-muted px-1 py-0.5 font-medium text-muted-foreground">
              Social
            </span>
          )}
          <NewBadge publishedAt={article.published_at} />
        </div>

        <span className="text-xs font-semibold text-foreground group-hover:text-primary">
          {cleanScrapedText(article.title)}
        </span>

        {article.companies.length > 0 && (
          <div
            className="mt-auto flex flex-wrap gap-1 pt-1"
            onClick={(event) => event.stopPropagation()}
          >
            {article.companies.map((name) => (
              <CompanyBadge key={name} name={name} href={`/company/${encodeURIComponent(name)}`} />
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

function RadarPlaceholder() {
  return (
    <svg viewBox="0 0 24 24" className="size-8" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.3" strokeWidth="1.5" />
      <circle cx="12" cy="12" r="5" fill="none" stroke="currentColor" strokeOpacity="0.3" strokeWidth="1.5" />
      <circle cx="12" cy="12" r="1.25" fill="currentColor" />
    </svg>
  );
}
