import Link from "next/link";
import type { Article } from "@/lib/types";
import { CompanyBadge } from "./CompanyBadge";
import { AlertBadge } from "./AlertBadge";
import { formatRelative } from "@/lib/format";

export function ArticleCard({ article }: { article: Article }) {
  return (
    <article className="group flex flex-col overflow-hidden rounded-xl border border-border bg-card transition-colors hover:border-primary/50">
      <Link
        href={article.link}
        target="_blank"
        rel="noopener noreferrer"
        className="block aspect-[16/9] overflow-hidden bg-muted"
      >
        {article.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- external, unpredictable source hosts
          <img
            src={article.image_url}
            alt=""
            className="size-full object-cover transition-transform group-hover:scale-105"
            loading="lazy"
          />
        ) : (
          <div className="flex size-full items-center justify-center text-muted-foreground">
            <RadarPlaceholder />
          </div>
        )}
      </Link>

      <div className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>{formatRelative(article.published_at)}</span>
          {article.content_kind === "social" && (
            <span className="rounded-full bg-muted px-2 py-0.5 font-medium text-muted-foreground">
              Social
            </span>
          )}
          <AlertBadge level={article.alert_level} />
        </div>

        <Link
          href={article.link}
          target="_blank"
          rel="noopener noreferrer"
          className="font-semibold text-foreground group-hover:text-primary"
        >
          {article.title}
        </Link>

        {article.summary && (
          <p className="line-clamp-3 text-sm text-muted-foreground">{article.summary}</p>
        )}

        {article.companies.length > 0 && (
          <div className="mt-auto flex flex-wrap gap-1.5 pt-1">
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
