"use client";

import { useSyncExternalStore } from "react";
import { isPublishedToday } from "@/lib/format";

function subscribe() {
  return () => {};
}

// Same mount-gating trick as RelativeTime/ThemeToggle — isPublishedToday
// compares against the viewer's local "today", which can differ from the
// server's at render time, so this renders nothing until mounted rather
// than risk a hydration mismatch.
function useHasMounted() {
  return useSyncExternalStore(subscribe, () => true, () => false);
}

export function NewBadge({ publishedAt }: { publishedAt: string }) {
  const mounted = useHasMounted();
  if (!mounted || !isPublishedToday(publishedAt)) return null;

  return (
    <span className="rounded-full bg-primary px-2 py-0.5 text-xs font-medium text-primary-foreground">
      New
    </span>
  );
}
