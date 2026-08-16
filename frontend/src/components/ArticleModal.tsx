"use client";

import { useEffect } from "react";
import type { Article } from "@/lib/types";
import { AlertBadge } from "./AlertBadge";
import { CompanyBadge } from "./CompanyBadge";
import { CompanyLogoFallback } from "./CompanyLogoFallback";
import { RelativeTime } from "./RelativeTime";

// Detail view opened by clicking a card — a bigger, uncropped look at the
// photo plus the full headline/summary, with the actual jump to the source
// as one deliberate action inside it rather than the card's whole surface.
export function ArticleModal({ article, onClose }: { article: Article; onClose: () => void }) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={article.title}
        onClick={(event) => event.stopPropagation()}
        className="relative flex max-h-[85vh] w-full max-w-lg flex-col overflow-y-auto rounded-2xl border border-border bg-card"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute right-3 top-3 z-10 flex size-8 items-center justify-center rounded-full bg-background/80 text-foreground hover:bg-accent hover:text-accent-foreground"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-4" aria-hidden="true">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>

        {(article.image_url || article.companies[0]) && (
          <div className="aspect-[16/9] w-full shrink-0 overflow-hidden bg-muted">
            {article.image_url ? (
              // eslint-disable-next-line @next/next/no-img-element -- external, unpredictable source hosts
              <img src={article.image_url} alt="" className="size-full object-contain" />
            ) : (
              <CompanyLogoFallback company={article.companies[0]} />
            )}
          </div>
        )}

        <div className="flex flex-col gap-3 p-5">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span><RelativeTime publishedAt={article.published_at} /></span>
            {article.content_kind === "social" && (
              <span className="rounded-full bg-muted px-2 py-0.5 font-medium text-muted-foreground">
                Social
              </span>
            )}
            <AlertBadge level={article.alert_level} />
          </div>

          <h2 className="font-serif text-xl font-semibold leading-snug text-foreground">
            {article.title}
          </h2>

          {article.summary && <p className="text-sm text-muted-foreground">{article.summary}</p>}

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
            className="mt-2 inline-flex items-center justify-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            Read full {article.content_kind === "social" ? "post" : "article"}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-3.5" aria-hidden="true">
              <path d="M7 17L17 7M7 7h10v10" />
            </svg>
          </a>
        </div>
      </div>
    </div>
  );
}
