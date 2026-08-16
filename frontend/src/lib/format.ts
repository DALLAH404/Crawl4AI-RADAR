// published_at can be a bare "YYYY-MM-DD" or a full ISO timestamp (see
// read-api/README.md) — Date() parses both.
export function formatDate(published_at: string): string {
  if (!published_at) return "";
  const date = new Date(published_at);
  if (Number.isNaN(date.getTime())) return published_at;
  return date.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
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
