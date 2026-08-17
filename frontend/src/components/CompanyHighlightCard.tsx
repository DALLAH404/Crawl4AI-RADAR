"use client";

import { useState } from "react";
import Link from "next/link";
import type { Article } from "@/lib/types";
import { companyColor } from "@/lib/companyColor";
import { BookmarkButton } from "./BookmarkButton";
import { NewBadge } from "./NewBadge";
import { ArticleModal } from "./ArticleModal";
import { CompanyLogoFallback } from "./CompanyLogoFallback";
import { RelativeTime } from "./RelativeTime";

// One company's newest article, sized as the "big" card in the homepage's
// per-company highlights grid — bigger than ArticleCard (full-width image,
// serif headline, visible summary) but not the old single giant 2-col hero,
// since this repeats once per company rather than appearing alone.
export function CompanyHighlightCard({
  company,
  article,
}: {
  company: string;
  article: Article;
}) {
  const [open, setOpen] = useState(false);
  const { light, dark } = companyColor(company);

  return (
    <>
      <article
        onClick={() => setOpen(true)}
        className="flex cursor-pointer flex-col overflow-hidden rounded-md bg-card shadow-lg shadow-gray-500/20 transition-[transform,box-shadow] hover:scale-[1.03] hover:shadow-2xl hover:shadow-gray-500/30"
      >
        <div className="relative block aspect-[2/1] overflow-hidden bg-muted">
          {article.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element -- external, unpredictable source hosts
            <img src={article.image_url} alt="" className="size-full object-cover" loading="lazy" />
          ) : (
            <CompanyLogoFallback company={company} />
          )}
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-teal-500/25 via-transparent to-orange-500/25 mix-blend-overlay" />
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/20 to-transparent" />
          <div className="absolute right-1 top-1 rounded-full bg-background/80 backdrop-blur-sm">
            <BookmarkButton article={article} size="sm" />
          </div>
          <div className="absolute inset-x-0 bottom-0 h-1 dark:hidden" style={{ backgroundColor: light }} aria-hidden="true" />
          <div className="absolute inset-x-0 bottom-0 hidden h-1 dark:block" style={{ backgroundColor: dark }} aria-hidden="true" />
        </div>

        <div className="flex flex-1 flex-col gap-1.5 p-2.5">
          <Link
            href={`/company/${encodeURIComponent(company)}`}
            onClick={(event) => event.stopPropagation()}
            className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground"
          >
            <span className="size-1.5 rounded-full dark:hidden" style={{ backgroundColor: light }} aria-hidden="true" />
            <span className="hidden size-1.5 rounded-full dark:inline-block" style={{ backgroundColor: dark }} aria-hidden="true" />
            {company}
          </Link>

          <span className="font-serif text-sm font-semibold leading-snug text-foreground">
            {article.title}
          </span>

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

          {article.summary && (
            <p className="line-clamp-2 text-[11px] text-muted-foreground">{article.summary}</p>
          )}
        </div>
      </article>

      {open && <ArticleModal article={article} onClose={() => setOpen(false)} />}
    </>
  );
}
