"use client";

import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { companyColor } from "@/lib/companyColor";

const KIND_LABELS: Record<string, string> = { news: "News", social: "Social" };

// Removable pills summarizing the current company + kind filter, so the
// active state stays visible even after the dropdown/toggle above closes.
export function ActiveFilters() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const companies = (searchParams.get("company") ?? "").split(",").filter(Boolean);
  const kind = searchParams.get("kind") ?? "";

  if (companies.length === 0 && !kind) return null;

  function removeCompany(name: string) {
    const params = new URLSearchParams(searchParams.toString());
    const next = companies.filter((c) => c !== name);
    if (next.length) {
      params.set("company", next.join(","));
    } else {
      params.delete("company");
    }
    router.push(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  function removeKind() {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("kind");
    router.push(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {kind && (
        <button
          type="button"
          onClick={removeKind}
          className="inline-flex items-center gap-1 rounded-full border border-border bg-card px-2 py-0.5 text-xs font-medium text-card-foreground hover:bg-accent hover:text-accent-foreground"
        >
          {KIND_LABELS[kind] ?? kind}
          <RemoveIcon />
        </button>
      )}
      {companies.map((name) => {
        const { light, dark } = companyColor(name);
        return (
          <button
            key={name}
            type="button"
            onClick={() => removeCompany(name)}
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-2 py-0.5 text-xs font-medium text-card-foreground hover:bg-accent hover:text-accent-foreground"
          >
            <span className="size-1.5 rounded-full dark:hidden" style={{ backgroundColor: light }} aria-hidden="true" />
            <span className="hidden size-1.5 rounded-full dark:inline-block" style={{ backgroundColor: dark }} aria-hidden="true" />
            {name}
            <RemoveIcon />
          </button>
        );
      })}
    </div>
  );
}

function RemoveIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className="size-3" aria-hidden="true">
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}
