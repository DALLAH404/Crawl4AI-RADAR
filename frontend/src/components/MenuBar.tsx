import Link from "next/link";
import { CompaniesMenu } from "./CompaniesMenu";

// Persistent menu bar below the hero — Companies dropdown + a Bookmarks
// link for now, built to hold more sections/buttons later without
// restructuring this container.
export function MenuBar() {
  return (
    <nav className="border-b border-border bg-card shadow-lg shadow-gray-500/20">
      <div className="mx-auto flex max-w-6xl items-center gap-2 px-4">
        <CompaniesMenu />
        <Link
          href="/bookmarks"
          className="flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium text-foreground hover:bg-accent hover:text-accent-foreground"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-3.5" aria-hidden="true">
            <path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z" strokeLinejoin="round" />
          </svg>
          Bookmarks
        </Link>
      </div>
    </nav>
  );
}
