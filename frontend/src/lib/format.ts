// published_at can be a bare "YYYY-MM-DD" or a full ISO timestamp (see
// read-api/README.md) — Date() parses both.
//
// timeZone: "UTC" is required, not cosmetic — without it this renders in
// the runtime's local timezone, which is UTC on the server (Node) but the
// viewer's own offset in the browser. For a published_at near midnight
// UTC, that's a different calendar date on each side: a real server/client
// hydration mismatch (SSR said "18 Aug", the client's first paint said "19
// Aug"), not just a display preference.
export function formatDate(published_at: string): string {
  if (!published_at) return "";
  const date = new Date(published_at);
  if (Number.isNaN(date.getTime())) return published_at;
  return date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function formatRelative(published_at: string): string {
  if (!published_at) return "";
  const date = new Date(published_at);
  if (Number.isNaN(date.getTime())) return published_at;

  const diffMs = Date.now() - date.getTime();
  const diffHours = Math.round(diffMs / (1000 * 60 * 60));
  if (diffHours < 1) return "just now";
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.round(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return formatDate(published_at);
}

// Compares calendar dates in the *viewer's* local timezone, which is why
// NewBadge (the only caller) is client-mount-gated rather than calling this
// directly during a shared server/client render — the server's "today" can
// differ from a viewer's near a local midnight, the same class of mismatch
// formatRelative had.
export function isPublishedToday(published_at: string): boolean {
  if (!published_at) return false;
  const date = new Date(published_at);
  if (Number.isNaN(date.getTime())) return false;
  const today = new Date();
  return (
    date.getFullYear() === today.getFullYear() &&
    date.getMonth() === today.getMonth() &&
    date.getDate() === today.getDate()
  );
}
