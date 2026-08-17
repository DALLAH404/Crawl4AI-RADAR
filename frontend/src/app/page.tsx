import { getArticles } from "@/lib/api";
import { Hero } from "@/components/Hero";
import { MenuBar } from "@/components/MenuBar";
import { StockTicker } from "@/components/StockTicker";
import { CompanyCarousel } from "@/components/CompanyCarousel";
import { CompanyHighlightCard } from "@/components/CompanyHighlightCard";
import { FilterSidebar } from "@/components/FilterSidebar";
import type { Article, ContentKind } from "@/lib/types";

const CAROUSEL_SIZE = 8;

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

function parseDate(value: string | string[] | undefined): string | undefined {
  const raw = Array.isArray(value) ? value[0] : value;
  return raw || undefined;
}

// The read API's own from/to only take effect in per-company query mode
// (see read-api/README.md), so date filtering is done here instead, against
// whatever's already been fetched — that way it works the same whether or
// not a company filter is active, rather than silently doing nothing in the
// unfiltered case.
function withinRange(publishedAt: string, from: string | undefined, to: string | undefined): boolean {
  if (!from && !to) return true;
  const date = publishedAt.slice(0, 10);
  if (from && date < from) return false;
  if (to && date > to) return false;
  return true;
}

// Two different jobs depending on whether a company filter is active:
//
// - No company selected: one highlight per company, its single newest
//   article — a broad "most active right now" overview. There's no "list
//   all companies with activity" endpoint, so this pulls a wide window of
//   the latest-overall feed and takes each company's first (= newest,
//   since the feed is already recency-sorted) in-range appearance —
//   companies whose last mention falls outside that window just won't have
//   a card, the right tradeoff against firing one API call per tracked
//   company on every homepage load. Order is "most recently active first",
//   which the carousel relies on.
// - Specific companies selected: every matching article for each, not just
//   the newest — once you've deliberately picked a company, seeing only
//   its single latest post instead of everything in the selected
//   timeframe/kind is surprising, not a highlight.
async function getCompanyHighlights(
  company: string[] | undefined,
  kind: ContentKind | undefined,
  from: string | undefined,
  to: string | undefined,
): Promise<{ company: string; article: Article }[]> {
  if (company?.length) {
    const results = await Promise.all(
      company.map(async (name) => {
        const { items } = await getArticles({ company: [name], kind, from, to, limit: 30 });
        return items
          .filter((article) => withinRange(article.published_at, from, to))
          .map((article) => ({ company: name, article }));
      }),
    );
    return results.flat();
  }

  const { items } = await getArticles({ kind, limit: 100 });
  const newestByCompany = new Map<string, Article>();
  for (const article of items) {
    if (!withinRange(article.published_at, from, to)) continue;
    for (const name of article.companies) {
      if (!newestByCompany.has(name)) {
        newestByCompany.set(name, article);
      }
    }
  }
  return [...newestByCompany.entries()].map(([company, article]) => ({ company, article }));
}

export default async function Home({ searchParams }: PageProps<"/">) {
  const params = await searchParams;
  const company = parseCompanies(params.company);
  const kind = parseKind(params.kind);
  const from = parseDate(params.from);
  const to = parseDate(params.to);

  // The carousel is deliberately unfiltered — a fixed "most active right
  // now" spotlight, not something that should shrink to whatever's
  // currently filtered in the grid below. Reused as the grid's own data
  // when no filter is active, since it'd otherwise be an identical fetch.
  const unfiltered = await getCompanyHighlights(undefined, undefined, undefined, undefined);
  const highlights =
    company || kind || from || to
      ? await getCompanyHighlights(company, kind, from, to)
      : unfiltered;

  return (
    <div className="flex flex-1 flex-col">
      <StockTicker />
      <Hero />
      <MenuBar />
      <CompanyCarousel highlights={unfiltered.slice(0, CAROUSEL_SIZE)} />
      <main className="flex w-full flex-1 flex-col gap-6 px-6 py-6 lg:flex-row lg:gap-8">
        <FilterSidebar />

        <div className="max-w-5xl flex-1">
          {highlights.length === 0 ? (
            <EmptyState />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {highlights.map(({ company, article }) => (
                <CompanyHighlightCard key={article.article_hash} company={company} article={article} />
              ))}
            </div>
          )}
        </div>
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
