import { getArticles } from "@/lib/api";
import { Hero } from "@/components/Hero";
import { MenuBar } from "@/components/MenuBar";
import { StockTicker } from "@/components/StockTicker";
import { CompanyCarousel } from "@/components/CompanyCarousel";
import { ArticleFeed } from "@/components/ArticleFeed";
import { FilterSidebar } from "@/components/FilterSidebar";
import type { Article, ContentKind } from "@/lib/types";

const CAROUSEL_SIZE = 8;
const PAGE_SIZE = 24;

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

function parseCursor(value: string | string[] | undefined): string | undefined {
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

// The carousel's own data, independent of the grid below and of any of the
// page's filters — a fixed "most active right now" spotlight, one highlight
// per company (its single newest article). There's no "list all companies
// with activity" endpoint, so this pulls a wide window of the latest-overall
// feed and takes each company's first (= newest, since the feed is already
// recency-sorted) appearance — companies whose last mention falls outside
// that window just won't have a spot, the right tradeoff against firing one
// API call per tracked company on every homepage load.
async function getCarouselHighlights(): Promise<{ company: string; article: Article }[]> {
  const { items } = await getArticles({ limit: 100 });
  const newestByCompany = new Map<string, Article>();
  for (const article of items) {
    for (const name of article.companies) {
      if (!newestByCompany.has(name)) {
        newestByCompany.set(name, article);
      }
    }
  }
  return [...newestByCompany.entries()].map(([company, article]) => ({ company, article }));
}

// The main feed: every article across every company, newest first — not
// deduped down to one per company (that's the carousel's job above).
async function getFeed(
  company: string[] | undefined,
  kind: ContentKind | undefined,
  from: string | undefined,
  to: string | undefined,
  cursor: string | undefined,
): Promise<{ items: Article[]; nextCursor: string | null }> {
  if (company?.length) {
    // One Query per company (CompanyTimeIndex can't take more than one
    // partition key at a time), merged back into a single newest-first
    // list — each company's own results already come back newest-first,
    // but the merge itself doesn't preserve that across companies without
    // this sort.
    const results = await Promise.all(
      company.map((name) => getArticles({ company: [name], kind, from, to, limit: PAGE_SIZE })),
    );
    const items = results
      .flatMap((r) => r.items)
      .filter((article) => withinRange(article.published_at, from, to))
      .sort((a, b) => (a.published_at > b.published_at ? -1 : 1));
    // Multi-company reads are already an approximate, per-company-cursor
    // pagination on the read API's side (read-api/README.md) — not
    // layering a second "Load more" on top of that here.
    return { items, nextCursor: null };
  }

  if (from || to) {
    // Same reasoning as withinRange's own comment: filters client-side
    // against one wider fetch instead of relying on the API. A cursor
    // wouldn't paginate cleanly on top of a client-side filter — a page
    // could come back empty while more matches exist further back — so
    // no "Load more" for this case either.
    const { items } = await getArticles({ kind, limit: 100 });
    return { items: items.filter((a) => withinRange(a.published_at, from, to)), nextCursor: null };
  }

  const { items, next_cursor } = await getArticles({ kind, limit: PAGE_SIZE, cursor });
  return { items, nextCursor: next_cursor };
}

export default async function Home({ searchParams }: PageProps<"/">) {
  const params = await searchParams;
  const company = parseCompanies(params.company);
  const kind = parseKind(params.kind);
  const from = parseDate(params.from);
  const to = parseDate(params.to);
  const cursor = parseCursor(params.cursor);

  const [carouselHighlights, { items, nextCursor }] = await Promise.all([
    getCarouselHighlights(),
    getFeed(company, kind, from, to, cursor),
  ]);

  return (
    <div className="flex flex-1 flex-col">
      <StockTicker />
      <Hero />
      <MenuBar />
      <CompanyCarousel highlights={carouselHighlights.slice(0, CAROUSEL_SIZE)} />
      <main className="flex w-full flex-1 flex-col gap-6 px-6 py-6 lg:flex-row lg:gap-8">
        <FilterSidebar />

        <div className="max-w-5xl flex-1">
          <ArticleFeed
            key={`${company?.join(",") ?? ""}|${kind ?? ""}|${from ?? ""}|${to ?? ""}|${cursor ?? ""}`}
            initialItems={items}
            initialCursor={nextCursor}
            kind={kind}
          />
        </div>
      </main>
    </div>
  );
}
