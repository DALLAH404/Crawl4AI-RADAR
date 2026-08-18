import Image from "next/image";
import Link from "next/link";
import { ThemeToggle } from "./ThemeToggle";

export function Header() {
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/95 backdrop-blur-sm">
      <div className="relative mx-auto flex max-w-5xl items-center justify-end px-4 py-3">
        <Link href="/" className="absolute left-1/2 -translate-x-1/2">
          <Image src="/logo.png" alt="RADAR" width={97} height={36} priority className="h-9 w-auto" />
        </Link>
        <ThemeToggle />
      </div>
    </header>
  );
}
