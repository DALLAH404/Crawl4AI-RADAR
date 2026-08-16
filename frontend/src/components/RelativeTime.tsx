"use client";

import { useSyncExternalStore } from "react";
import { formatDate, formatRelative } from "@/lib/format";

function subscribe() {
  return () => {};
}

// Same mount-gating trick as ThemeToggle.tsx's useHasMounted — formatRelative
// calls Date.now() at render time, so the server's render pass and the
// client's hydration pass compute different strings ("5h ago" vs "8h ago")
// whenever there's any gap between the two, which is a hydration mismatch,
// not just a cosmetic one-render flicker. formatDate has no such dependency
// (it only formats the given timestamp), so it's what both the server and
// the client's first paint render; only after mount does it switch to the
// relative text.
function useHasMounted() {
  return useSyncExternalStore(subscribe, () => true, () => false);
}

export function RelativeTime({ publishedAt }: { publishedAt: string }) {
  const mounted = useHasMounted();
  return <>{mounted ? formatRelative(publishedAt) : formatDate(publishedAt)}</>;
}
