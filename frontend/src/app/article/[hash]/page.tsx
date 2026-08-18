import { MenuBar } from "@/components/MenuBar";
import { ArticleDetail } from "@/components/ArticleDetail";

export const metadata = { title: "Article" };

export default async function ArticlePage({ params }: PageProps<"/article/[hash]">) {
  const { hash } = await params;

  return (
    <div className="flex flex-1 flex-col">
      <MenuBar />
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-6">
        <ArticleDetail hash={decodeURIComponent(hash)} />
      </main>
    </div>
  );
}
