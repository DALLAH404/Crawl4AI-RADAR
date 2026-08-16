import Link from "next/link";
import type { Article } from "@/lib/types";
import { CompanyBadge } from "./CompanyBadge";
import { AlertBadge } from "./AlertBadge";
import { formatRelative } from "@/lib/format";

// The most-recent article gets the editorial treatment — bigger image, serif
// headline — everything else falls into the plain grid via ArticleCard.
export function FeaturedArticle({ article }: { article: Article }) {
  return (
    <article className="grid overflow-hidden rounded-2xl border border-border bg-card md:grid-cols-2">
      <Link
        href={article.link}
        target="_blank"
        rel="noopener noreferrer"
        className="block aspect-[16/9] overflow-hidden bg-muted md:aspect-auto md:h-full"
      >
        {article.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- external, unpredictable source hosts
          <img src={article.image_url} alt="" className="size-full object-cover" />
        ) : null}
      </Link>

      <div className="flex flex-col gap-4 p-6">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="rounded-full bg-primary px-2 py-0.5 font-medium text-primary-foreground">
            Latest
          </span>
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
          className="font-serif text-2xl font-semibold leading-tight text-foreground hover:text-primary md:text-3xl"
        >
          {article.title}
        </Link>

        {article.summary && (
          <p className="line-clamp-4 text-sm text-muted-foreground">{article.summary}</p>
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
