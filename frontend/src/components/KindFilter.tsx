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

  function select(value: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set("kind", value);
    else params.delete("kind");
    router.push(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  return (
    <div>
      <span className="mb-2 block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Kind
      </span>
      <ul className="space-y-0.5">
        {OPTIONS.map((option) => {
          const checked = option.value === current;
          return (
            <li key={option.value}>
              <label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm text-foreground hover:bg-accent hover:text-accent-foreground">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => select(checked ? "" : option.value)}
                  className="size-3.5 rounded border-border accent-[var(--primary)]"
                />
                {option.label}
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
