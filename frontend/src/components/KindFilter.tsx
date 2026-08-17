"use client";

import { useRouter, useSearchParams, usePathname } from "next/navigation";

// Checkbox-styled version of KindToggle, matching CompanyFilter's list
// below it in the sidebar. Still mutually exclusive under the hood — the
// API's kind param is a single value or absent — checking one unchecks the
// others, and unchecking the active one falls back to "All".
const OPTIONS: { value: "" | "news" | "social"; label: string }[] = [
  { value: "", label: "All" },
  { value: "news", label: "News" },
  { value: "social", label: "Social" },
];

export function KindFilter() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const current = searchParams.get("kind") ?? "";
  const active = current !== "";

  function select(value: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set("kind", value);
    else params.delete("kind");
    router.push(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  return (
    <div className={`border-l-2 pl-2.5 ${active ? "border-primary" : "border-transparent"}`}>
      <span className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className={`size-3.5 ${active ? "text-primary" : ""}`}
          aria-hidden="true"
        >
          <path d="M4 6h16M4 12h16M4 18h16" strokeLinecap="round" />
        </svg>
        Kind
      </span>
      <div className="flex rounded-full bg-muted p-0.5">
        {OPTIONS.map((option) => {
          const checked = option.value === current;
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => select(option.value)}
              aria-pressed={checked}
              className={`flex-1 rounded-full px-2 py-1 text-xs font-medium transition-colors ${
                checked
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
