// alert_level comes from the API as Alto/Medio/Baixo (see
// ai-crawling-pipeline/src/radar_pipeline/summarize/prompts.py's ALERT_LEVELS)
// — translated here for display only, the raw value is still what drives the
// severity check below. Only "Alto" (high) gets the destructive treatment —
// the palette has no dedicated warning/amber hue, so Medio/Baixo/unset stay
// neutral rather than inventing a color the palette doesn't define. Label
// always accompanies color, never color alone.
const LABELS: Record<string, string> = {
  Alto: "High",
  Medio: "Medium",
  Baixo: "Low",
};

export function AlertBadge({ level }: { level: string }) {
  if (!level) return null;

  const isHigh = level === "Alto";

  return (
    <span
      className={
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium " +
        (isHigh
          ? "bg-destructive/10 text-destructive"
          : "bg-muted text-muted-foreground")
      }
    >
      {isHigh && (
        <svg viewBox="0 0 24 24" fill="currentColor" className="size-2.5" aria-hidden="true">
          <circle cx="12" cy="12" r="6" />
        </svg>
      )}
      {LABELS[level] ?? level}
    </span>
  );
}
