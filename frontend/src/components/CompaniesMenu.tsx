"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { COMPANIES } from "@/lib/companies";
import { companyColor } from "@/lib/companyColor";

// Pure navigation, not a filter — clicking a company jumps straight to its
// page, unlike FilterSidebar's CompanyFilter which narrows the current page.
export function CompaniesMenu() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium text-foreground hover:bg-accent hover:text-accent-foreground"
      >
        Companies
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
        <div className="absolute z-20 mt-2 max-h-96 w-64 overflow-y-auto rounded-lg border border-border bg-popover p-2 shadow-lg">
          <ul className="space-y-0.5">
            {COMPANIES.map((name) => {
              const { light, dark } = companyColor(name);
              return (
                <li key={name}>
                  <Link
                    href={`/company/${encodeURIComponent(name)}`}
                    onClick={() => setOpen(false)}
                    className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-popover-foreground hover:bg-accent hover:text-accent-foreground"
                  >
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
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
