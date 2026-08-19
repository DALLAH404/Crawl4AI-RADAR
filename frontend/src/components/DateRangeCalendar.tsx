"use client";

import { useMemo, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";

const WEEKDAY_LABELS = ["S", "M", "T", "W", "T", "F", "S"];

function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

// A single month-grid calendar for picking a from/to range in one place —
// click a start day, then an end day; both land in the URL together in one
// navigation, rather than two separate "From"/"To" inputs.
export function DateRangeCalendar() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const from = searchParams.get("from") ?? "";
  const to = searchParams.get("to") ?? "";

  const [viewMonth, setViewMonth] = useState(() => startOfMonth(from ? new Date(from) : new Date()));
  const [draftFrom, setDraftFrom] = useState(from);
  const [draftTo, setDraftTo] = useState(to);

  function applyRange(nextFrom: string, nextTo: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (nextFrom) params.set("from", nextFrom);
    else params.delete("from");
    if (nextTo) params.set("to", nextTo);
    else params.delete("to");
    // See KindFilter's comment — a cursor from a different filter
    // combination is an invalid ExclusiveStartKey for this one.
    params.delete("cursor");
    router.push(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  function handleDayClick(iso: string) {
    if (!draftFrom || (draftFrom && draftTo)) {
      setDraftFrom(iso);
      setDraftTo("");
      return;
    }
    const [rangeFrom, rangeTo] = iso < draftFrom ? [iso, draftFrom] : [draftFrom, iso];
    setDraftFrom(rangeFrom);
    setDraftTo(rangeTo);
    applyRange(rangeFrom, rangeTo);
  }

  function clear() {
    setDraftFrom("");
    setDraftTo("");
    applyRange("", "");
  }

  const year = viewMonth.getFullYear();
  const month = viewMonth.getMonth();
  const firstWeekday = startOfMonth(viewMonth).getDay();
  const totalDays = new Date(year, month + 1, 0).getDate();

  const cells = useMemo(() => {
    const list: (string | null)[] = [];
    for (let i = 0; i < firstWeekday; i++) list.push(null);
    for (let day = 1; day <= totalDays; day++) {
      list.push(`${year}-${pad(month + 1)}-${pad(day)}`);
    }
    return list;
  }, [year, month, firstWeekday, totalDays]);

  const active = Boolean(draftFrom || draftTo);

  return (
    <div className={`border-l-2 pl-2.5 ${active ? "border-primary" : "border-transparent"}`}>
      <div className="flex items-center justify-between pb-2">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className={`size-3.5 ${active ? "text-primary" : ""}`}
            aria-hidden="true"
          >
            <rect x="3" y="4" width="18" height="17" rx="2" />
            <path d="M3 9h18M8 2v4M16 2v4" strokeLinecap="round" />
          </svg>
          Timeframe
        </span>
        {(draftFrom || draftTo) && (
          <button
            type="button"
            onClick={clear}
            className="text-xs font-medium text-primary hover:underline"
          >
            Clear
          </button>
        )}
      </div>

      <div className="rounded-lg border border-border bg-card p-3">
        <div className="mb-2 flex items-center justify-between">
          <button
            type="button"
            onClick={() => setViewMonth(new Date(year, month - 1, 1))}
            aria-label="Previous month"
            className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-accent-foreground"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-3.5" aria-hidden="true">
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </button>
          <span className="text-xs font-medium text-foreground">
            {viewMonth.toLocaleDateString("en-US", { month: "long", year: "numeric" })}
          </span>
          <button
            type="button"
            onClick={() => setViewMonth(new Date(year, month + 1, 1))}
            aria-label="Next month"
            className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-accent-foreground"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-3.5" aria-hidden="true">
              <path d="M9 6l6 6-6 6" />
            </svg>
          </button>
        </div>

        <div className="grid grid-cols-7 text-center text-[10px] text-muted-foreground">
          {WEEKDAY_LABELS.map((label, i) => (
            <span key={i}>{label}</span>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-y-1 text-center text-xs">
          {cells.map((iso, i) => {
            if (!iso) return <span key={i} />;
            const isEndpoint = iso === draftFrom || iso === draftTo;
            const inRange = draftFrom && draftTo && iso > draftFrom && iso < draftTo;
            return (
              <button
                key={iso}
                type="button"
                onClick={() => handleDayClick(iso)}
                className={
                  "mx-auto flex size-6 items-center justify-center rounded-full " +
                  (isEndpoint
                    ? "bg-primary text-primary-foreground"
                    : inRange
                      ? "bg-primary/15 text-foreground"
                      : "text-foreground hover:bg-accent hover:text-accent-foreground")
                }
              >
                {Number(iso.slice(8, 10))}
              </button>
            );
          })}
        </div>
      </div>

      <p className="mt-2 text-xs text-muted-foreground">
        {draftFrom && draftTo
          ? `${draftFrom} – ${draftTo}`
          : draftFrom
            ? "Pick an end date"
            : "Pick a start date"}
      </p>
    </div>
  );
}
