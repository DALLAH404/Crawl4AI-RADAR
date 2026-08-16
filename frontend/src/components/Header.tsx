import Link from "next/link";
import Image from "next/image";
import { ThemeToggle } from "./ThemeToggle";

export function Header() {
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/95 backdrop-blur-sm">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
        <Link href="/" className="flex items-center gap-2">
          {/* bg-white (not bg-background) is deliberate — the source logo has a
              flat white backdrop baked in with no alpha channel; a fixed white
              chip reads as an intentional badge in both themes instead of a
              stray box against the dark header. */}
          <span className="flex size-7 items-center justify-center overflow-hidden rounded-full bg-white ring-1 ring-border">
            <Image src="/logo.jpg" alt="" width={28} height={28} className="size-full object-cover" />
          </span>
          <span className="font-sans text-lg font-bold tracking-tight text-foreground">
            RADAR
          </span>
        </Link>
        <ThemeToggle />
      </div>
    </header>
  );
}
