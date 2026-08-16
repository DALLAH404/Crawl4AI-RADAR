"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { COMPANIES } from "@/lib/companies";
import { companyColor } from "@/lib/companyColor";

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
    const params = new URLSearchParams(searchParams.toString());
    if (next.length) {
      params.set("company", next.join(","));
    } else {
      params.delete("company");
    }
    router.push(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  function toggleCompany(name: string) {
    const next = selected.includes(name)
      ? selected.filter((c) => c !== name)
      : [...selected, name];
    applySelection(next);
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-1.5 text-sm font-medium text-card-foreground hover:bg-accent hover:text-accent-foreground"
      >
        Companies{selected.length ? ` (${selected.length})` : ""}
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
        <div className="absolute z-20 mt-2 max-h-80 w-64 overflow-y-auto rounded-lg border border-border bg-popover p-2 shadow-lg">
          <div className="flex items-center justify-between px-2 pb-2">
            <span className="text-xs font-medium text-muted-foreground">
              Filter by company
            </span>
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
