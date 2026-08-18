"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import type { Article } from "@/lib/types";
import { companyColor } from "@/lib/companyColor";
import { stashArticle } from "@/lib/articleCache";
import { cleanScrapedText } from "@/lib/cleanScrapedText";
import { CompanyLogoFallback } from "./CompanyLogoFallback";
import { RelativeTime } from "./RelativeTime";

const SLIDE_MS = 10000;

// Auto-advancing spotlight — one company's newest article at a time in the
// main viewer, cycling every 3s, with a clickable thumbnail rail below it to
// jump straight to any company instead of waiting. Independent of the page's
// own company/kind filters — it's a fixed pulse-of-the-radar strip, not
// something that should collapse to whatever's currently filtered below.
export function CompanyCarousel({
  highlights,
}: {
  highlights: { company: string; article: Article }[];
}) {
  const [index, setIndex] = useState(0);
  const router = useRouter();

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

  function openArticle() {
    stashArticle(article);
    router.push(`/article/${encodeURIComponent(article.article_hash)}`);
  }

  return (
    <div className="border-b border-border bg-muted/30">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-10 sm:flex-row sm:items-center">
        <button
          key={`${article.article_hash}-image`}
          type="button"
          onClick={openArticle}
          className="relative aspect-[16/9] w-full shrink-0 cursor-pointer overflow-hidden rounded-2xl bg-muted text-left [animation:carousel-fade-in_0.4s_ease-out] sm:w-[28rem]"
        >
          {article.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element -- external, unpredictable source hosts
            <img src={article.image_url} alt="" className="size-full object-cover" />
          ) : (
            <CompanyLogoFallback company={company} />
          )}
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-teal-500/25 via-transparent to-orange-500/25 mix-blend-overlay" />
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/20 to-transparent" />
        </button>

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
          <button
            type="button"
            onClick={openArticle}
            className="cursor-pointer text-left font-serif text-2xl font-semibold leading-snug text-foreground hover:text-primary sm:text-3xl"
          >
            {cleanScrapedText(article.title)}
          </button>
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
        <div className="mx-auto flex max-w-6xl justify-center gap-2 overflow-x-auto px-4 pb-6">
          {highlights.map((h, i) => {
            const active = i === index;
            const { light: tLight, dark: tDark } = companyColor(h.company);
            return (
              <button
                key={h.company}
                type="button"
                onClick={() => setIndex(i)}
                aria-label={`Show ${h.company}`}
                aria-current={active}
                className={
                  "flex shrink-0 flex-col items-center gap-1 rounded-lg p-1 transition-[opacity,transform] " +
                  (active ? "scale-105 opacity-100" : "opacity-50 hover:opacity-80")
                }
              >
                <span className="relative block size-14 shrink-0 overflow-hidden rounded-md bg-muted">
                  {h.article.image_url ? (
                    // eslint-disable-next-line @next/next/no-img-element -- external, unpredictable source hosts
                    <img src={h.article.image_url} alt="" className="size-full object-cover" />
                  ) : (
                    <CompanyLogoFallback company={h.company} />
                  )}
                  {active && (
                    <>
                      <span
                        className="pointer-events-none absolute inset-0 rounded-md dark:hidden"
                        style={{ boxShadow: `inset 0 0 0 2px ${tLight}, 0 2px 6px 0 rgb(0 0 0 / 0.25)` }}
                        aria-hidden="true"
                      />
                      <span
                        className="pointer-events-none absolute inset-0 hidden rounded-md dark:block"
                        style={{ boxShadow: `inset 0 0 0 2px ${tDark}, 0 2px 6px 0 rgb(0 0 0 / 0.25)` }}
                        aria-hidden="true"
                      />
                    </>
                  )}
                </span>
                <span className="max-w-14 truncate text-[10px] font-medium text-muted-foreground">
                  {h.company}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
