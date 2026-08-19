"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { COMPANIES } from "@/lib/companies";
import { companyColor } from "@/lib/companyColor";

const WATCHLIST_KEY = "radar:watchlist";

function readWatchlist(): string[] {
  try {
    const saved = localStorage.getItem(WATCHLIST_KEY);
    const parsed = saved ? JSON.parse(saved) : [];
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : [];
  } catch {
    return [];
  }
}

export function CompanyFilter() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const selected = useMemo(() => {
    const raw = searchParams.get("company");
    return raw ? raw.split(",").filter(Boolean) : [];
  }, [searchParams]);

  // Restore the saved watchlist whenever the URL has no company filter of
  // its own — router.replace (not push) since this is resuming state, not
  // a fresh navigation the user should have to "back" out of. Naturally
  // only fires once per empty-filter visit: applying it changes
  // searchParams, which re-runs this effect and immediately short-circuits
  // on the now-present company param.
  useEffect(() => {
    if (searchParams.get("company")) return;
    const watchlist = readWatchlist();
    if (watchlist.length === 0) return;
    const params = new URLSearchParams(searchParams.toString());
    params.set("company", watchlist.join(","));
    // See applySelection's comment — a cursor from a different filter
    // combination is an invalid ExclusiveStartKey for this one.
    params.delete("cursor");
    router.replace(`${pathname}?${params}`, { scroll: false });
  }, [searchParams, pathname, router]);

  useEffect(() => {
    function onClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  function applySelection(next: string[]) {
    if (next.length) {
      localStorage.setItem(WATCHLIST_KEY, JSON.stringify(next));
    } else {
      localStorage.removeItem(WATCHLIST_KEY);
    }
    const params = new URLSearchParams(searchParams.toString());
    if (next.length) {
      params.set("company", next.join(","));
    } else {
      params.delete("company");
    }
    // A cursor encodes a pagination position for one specific query shape
    // (which index, which filters) — carrying it into a different filter
    // combination sends DynamoDB an ExclusiveStartKey that doesn't match
    // the index actually being queried, which the read API can't recover
    // from (500, not just wrong results).
    params.delete("cursor");
    router.push(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  function toggleCompany(name: string) {
    const next = selected.includes(name)
      ? selected.filter((c) => c !== name)
      : [...selected, name];
    applySelection(next);
  }

  const active = selected.length > 0;

  return (
    <div className={`relative border-l-2 pl-2.5 ${active ? "border-primary" : "border-transparent"}`} ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between rounded-md border border-border bg-card px-2 py-1.5 text-sm font-medium text-card-foreground hover:bg-accent hover:text-accent-foreground"
      >
        <span className="flex items-center gap-1.5">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className={`size-3.5 ${active ? "text-primary" : "text-muted-foreground"}`}
            aria-hidden="true"
          >
            <path d="M3 21h18M6 21V7l6-4 6 4v14M10 10h.01M14 10h.01M10 14h.01M14 14h.01" strokeLinecap="round" />
          </svg>
          Companies{selected.length ? ` (${selected.length})` : ""}
        </span>
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className={`size-3.5 transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {open && (
        <div className="absolute z-20 mt-2 max-h-96 w-full min-w-64 overflow-y-auto rounded-lg border border-border bg-popover p-2 shadow-lg">
          <div className="flex items-center justify-between px-2 pb-2">
            <span className="text-xs font-medium text-muted-foreground">Filter by company</span>
            {selected.length > 0 && (
              <button
                type="button"
                onClick={() => applySelection([])}
                className="text-xs font-medium text-primary hover:underline"
              >
                Clear
              </button>
            )}
          </div>
          <ul className="space-y-0.5">
            {COMPANIES.map((name) => {
              const checked = selected.includes(name);
              const { light, dark } = companyColor(name);
              return (
                <li key={name}>
                  <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm text-popover-foreground hover:bg-accent hover:text-accent-foreground">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleCompany(name)}
                      className="size-3.5 rounded border-border accent-[var(--primary)]"
                    />
                    <span
                      className="size-2 rounded-full dark:hidden"
                      style={{ backgroundColor: light }}
                      aria-hidden="true"
                    />
                    <span
                      className="hidden size-2 rounded-full dark:inline-block"
                      style={{ backgroundColor: dark }}
                      aria-hidden="true"
                    />
                    {name}
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
