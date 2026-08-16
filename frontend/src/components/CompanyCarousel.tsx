"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { Article } from "@/lib/types";
import { companyColor } from "@/lib/companyColor";
import { CompanyLogoFallback } from "./CompanyLogoFallback";
import { RelativeTime } from "./RelativeTime";

const SLIDE_MS = 3000;

// Auto-advancing spotlight — one company's newest article at a time, cycling
// every 3s, with clickable progress dots (a "carousel"). Independent of the
// page's own company/kind filters — it's a fixed pulse-of-the-radar strip,
// not something that should collapse to whatever's currently filtered below.
export function CompanyCarousel({
  highlights,
}: {
  highlights: { company: string; article: Article }[];
}) {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (highlights.length <= 1) return;
    const interval = setInterval(() => {
      setIndex((i) => (i + 1) % highlights.length);
    }, SLIDE_MS);
    return () => clearInterval(interval);
  }, [highlights.length]);

  if (highlights.length === 0) return null;

  const { company, article } = highlights[index];
  const { light, dark } = companyColor(company);

  return (
    <div className="border-b border-border bg-muted/30">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-10 sm:flex-row sm:items-center">
        <Link
          key={`${article.article_hash}-image`}
          href={article.link}
          target="_blank"
          rel="noopener noreferrer"
          className="relative aspect-[16/9] w-full shrink-0 overflow-hidden rounded-2xl bg-muted [animation:carousel-fade-in_0.4s_ease-out] sm:w-[28rem]"
        >
          {article.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element -- external, unpredictable source hosts
            <img src={article.image_url} alt="" className="size-full object-cover" />
          ) : (
            <CompanyLogoFallback company={company} />
          )}
        </Link>

        <div
          key={`${article.article_hash}-text`}
          className="flex flex-1 flex-col gap-3 [animation:carousel-fade-in_0.4s_ease-out]"
        >
          <Link
            href={`/company/${encodeURIComponent(company)}`}
            className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground"
          >
            <span className="size-2 rounded-full dark:hidden" style={{ backgroundColor: light }} aria-hidden="true" />
            <span className="hidden size-2 rounded-full dark:inline-block" style={{ backgroundColor: dark }} aria-hidden="true" />
            {company}
          </Link>
          <Link
            href={article.link}
            target="_blank"
            rel="noopener noreferrer"
            className="font-serif text-2xl font-semibold leading-snug text-foreground hover:text-primary sm:text-3xl"
          >
            {article.title}
          </Link>
          {article.summary && (
            <p className="line-clamp-2 max-w-xl text-sm text-muted-foreground sm:text-base">
              {article.summary}
            </p>
          )}
          <span className="text-xs text-muted-foreground">
            <RelativeTime publishedAt={article.published_at} />
          </span>
        </div>
      </div>

      {highlights.length > 1 && (
        <div className="flex items-center justify-center gap-1.5 pb-6">
          {highlights.map((h, i) => (
            <button
              key={h.company}
              type="button"
              onClick={() => setIndex(i)}
              aria-label={`Show ${h.company}`}
              aria-current={i === index}
              className={`h-1.5 rounded-full transition-all ${
                i === index ? "w-6 bg-primary" : "w-1.5 bg-muted-foreground/30"
              }`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
