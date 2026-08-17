import Link from "next/link";
import { ThemeToggle } from "./ThemeToggle";

export function Header() {
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/95 backdrop-blur-sm">
      <div className="relative mx-auto flex max-w-5xl items-center justify-end px-4 py-3">
        <Link
          href="/"
          className="absolute left-1/2 -translate-x-1/2 font-sans text-xl font-bold tracking-tight text-primary [text-shadow:0_1px_3px_rgba(0,0,0,0.2)] dark:[text-shadow:none]"
        >
          RADAR
        </Link>
        <ThemeToggle />
      </div>
    </header>
  );
}
