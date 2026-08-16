"use client";

import { useState } from "react";
import Link from "next/link";
import type { Article } from "@/lib/types";
import { companyColor } from "@/lib/companyColor";
import { AlertBadge } from "./AlertBadge";
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
        className="flex cursor-pointer flex-col overflow-hidden rounded-2xl border border-border bg-card"
      >
        <div className="block aspect-[16/9] overflow-hidden bg-muted">
          {article.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element -- external, unpredictable source hosts
            <img src={article.image_url} alt="" className="size-full object-cover" loading="lazy" />
          ) : (
            <CompanyLogoFallback company={company} />
          )}
        </div>

        <div className="flex flex-1 flex-col gap-3 p-5">
          <Link
            href={`/company/${encodeURIComponent(company)}`}
            onClick={(event) => event.stopPropagation()}
            className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground"
          >
            <span className="size-1.5 rounded-full dark:hidden" style={{ backgroundColor: light }} aria-hidden="true" />
            <span className="hidden size-1.5 rounded-full dark:inline-block" style={{ backgroundColor: dark }} aria-hidden="true" />
            {company}
          </Link>

          <span className="font-serif text-xl font-semibold leading-snug text-foreground">
            {article.title}
          </span>

          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span><RelativeTime publishedAt={article.published_at} /></span>
            {article.content_kind === "social" && (
              <span className="rounded-full bg-muted px-2 py-0.5 font-medium text-muted-foreground">
                Social
              </span>
            )}
            <AlertBadge level={article.alert_level} />
          </div>

          {article.summary && (
            <p className="line-clamp-2 text-sm text-muted-foreground">{article.summary}</p>
          )}
        </div>
      </article>

      {open && <ArticleModal article={article} onClose={() => setOpen(false)} />}
    </>
  );
}
