import Link from "next/link";
import type { Metadata } from "next";
import { getArticles } from "@/lib/api";
import { ArticleCard } from "@/components/ArticleCard";
import { KindToggle } from "@/components/KindToggle";
import { companyColor } from "@/lib/companyColor";
import type { ContentKind } from "@/lib/types";

function parseKind(value: string | string[] | undefined): ContentKind | undefined {
  const raw = Array.isArray(value) ? value[0] : value;
  return raw === "news" || raw === "social" ? raw : undefined;
}

export async function generateMetadata({
  params,
}: PageProps<"/company/[name]">): Promise<Metadata> {
  const { name } = await params;
  return { title: decodeURIComponent(name) };
}

export default async function CompanyPage({
  params,
  searchParams,
}: PageProps<"/company/[name]">) {
  const { name: rawName } = await params;
  const name = decodeURIComponent(rawName);
  const query = await searchParams;
  const kind = parseKind(query.kind);
  const cursorParam = Array.isArray(query.cursor) ? query.cursor[0] : query.cursor;

  const { items, next_cursor } = await getArticles({
    company: [name],
    kind,
    limit: 24,
    cursor: cursorParam,
  });

  const { light, dark } = companyColor(name);

  const nextHref = next_cursor
    ? `?${new URLSearchParams({ ...(kind ? { kind } : {}), cursor: next_cursor })}`
    : undefined;

  return (
    <div className="flex flex-1 flex-col">
      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-4 py-6">
        <div>
          <Link href="/" className="text-sm text-muted-foreground hover:text-foreground">
            ← All companies
          </Link>
          <div className="mt-2 flex items-center justify-between gap-3">
            <h1 className="flex items-center gap-2 font-serif text-2xl font-semibold text-foreground">
              <span
                className="size-3 rounded-full dark:hidden"
                style={{ backgroundColor: light }}
                aria-hidden="true"
              />
              <span
                className="hidden size-3 rounded-full dark:inline-block"
                style={{ backgroundColor: dark }}
                aria-hidden="true"
              />
              {name}
            </h1>
            <KindToggle />
          </div>
        </div>

        {items.length === 0 ? (
          <p className="py-16 text-center text-sm text-muted-foreground">
            No articles for {name} yet{kind ? ` in ${kind}` : ""}.
          </p>
        ) : (
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((article) => (
              <ArticleCard key={article.article_hash} article={article} />
            ))}
          </div>
        )}

        {nextHref && (
          <Link
            href={nextHref}
            className="mx-auto rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-accent hover:text-accent-foreground"
          >
            Load more
          </Link>
        )}
      </main>
    </div>
  );
}
