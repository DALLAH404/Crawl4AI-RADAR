import { getArticles } from "@/lib/api";
import { Hero } from "@/components/Hero";
import { FeaturedArticle } from "@/components/FeaturedArticle";
import { ArticleCard } from "@/components/ArticleCard";
import { CompanyFilter } from "@/components/CompanyFilter";
import { KindToggle } from "@/components/KindToggle";
import { ActiveFilters } from "@/components/ActiveFilters";
import type { ContentKind } from "@/lib/types";

function parseKind(value: string | string[] | undefined): ContentKind | undefined {
  const raw = Array.isArray(value) ? value[0] : value;
  return raw === "news" || raw === "social" ? raw : undefined;
}

function parseCompanies(value: string | string[] | undefined): string[] | undefined {
  const raw = Array.isArray(value) ? value[0] : value;
  if (!raw) return undefined;
  const companies = raw.split(",").filter(Boolean);
  return companies.length ? companies : undefined;
}

export default async function Home({ searchParams }: PageProps<"/">) {
  const params = await searchParams;
  const company = parseCompanies(params.company);
  const kind = parseKind(params.kind);

  const { items } = await getArticles({ company, kind, limit: 21 });
  const [featured, ...rest] = items;

  return (
    <div className="flex flex-1 flex-col">
      <Hero />
      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-4 py-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <CompanyFilter />
            <KindToggle />
          </div>
          <ActiveFilters />
        </div>

        {items.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="flex flex-col gap-6">
            {featured && <FeaturedArticle article={featured} />}
            {rest.length > 0 && (
              <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
                {rest.map((article) => (
                  <ArticleCard key={article.article_hash} article={article} />
                ))}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border py-24 text-center">
      <svg viewBox="0 0 24 24" className="size-10 text-muted-foreground" aria-hidden="true">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.4" strokeWidth="1.5" />
        <circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" strokeOpacity="0.4" strokeWidth="1.5" />
      </svg>
      <p className="font-medium text-foreground">Nothing on the radar yet</p>
      <p className="max-w-sm text-sm text-muted-foreground">
        No articles match this filter. Try clearing it or checking back after the next scan.
      </p>
    </div>
  );
}
