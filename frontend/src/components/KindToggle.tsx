"use client";

import { useRouter, useSearchParams, usePathname } from "next/navigation";

// Plain 3-way toggle (All/News/Social), not a color-coded legend — a binary
// filter isn't a categorical data series (dataviz skill judgment call).
const OPTIONS: { value: "" | "news" | "social"; label: string }[] = [
  { value: "", label: "All" },
  { value: "news", label: "News" },
  { value: "social", label: "Social" },
];

export function KindToggle() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const current = searchParams.get("kind") ?? "";

  function select(value: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (value) {
      params.set("kind", value);
    } else {
      params.delete("kind");
    }
    router.push(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  return (
    <div className="inline-flex rounded-md border border-border bg-muted p-0.5 text-sm">
      {OPTIONS.map((option) => {
        const active = option.value === current;
        return (
          <button
            key={option.value}
            type="button"
            onClick={() => select(option.value)}
            aria-pressed={active}
            className={
              "rounded-[calc(var(--radius-md)-2px)] px-3 py-1 font-medium transition-colors " +
              (active
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground")
            }
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
