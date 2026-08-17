import type { Metadata } from "next";
import { MenuBar } from "@/components/MenuBar";
import { BookmarksList } from "@/components/BookmarksList";

export const metadata: Metadata = { title: "Bookmarks" };

// Bookmarks live in localStorage only (see lib/bookmarks.ts) — this page is
// just a server-rendered shell (metadata, layout) around a client component
// that does the actual reading, since Server Components have no access to
// the browser's storage.
export default function BookmarksPage() {
  return (
    <div className="flex flex-1 flex-col">
      <MenuBar />
      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-6">
        <h1 className="font-serif text-2xl font-semibold text-foreground">Bookmarks</h1>
        <BookmarksList />
      </main>
    </div>
  );
}
