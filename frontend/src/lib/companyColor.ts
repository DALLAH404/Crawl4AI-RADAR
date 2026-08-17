// Categorical colors for company pill badges — the validated 8-slot
// palette from the dataviz skill's reference instance (references/palette.md),
// used as-is (no hue changes, so no re-validation needed).
//
// There are more companies in configs/radar_sources.yaml than validated
// slots, and a pill badge always shows the company name as text alongside
// the color — so per the skill's own rule ("identity is never color-alone"),
// text is the actual identifier and color is a secondary scanning aid. That
// means it's fine to *cycle* a company name into one of the 8 fixed slots via
// a stable hash rather than inventing a 9th+ hue (which the skill explicitly
// forbids) — two companies landing on the same slot is a minor cosmetic
// overlap, not an accessibility failure, because the label always disambiguates.
const COMPANY_COLOR_SLOTS: [light: string, dark: string][] = [
  ["#2a78d6", "#3987e5"], // blue
  ["#eb6834", "#d95926"], // orange
  ["#1baf7a", "#199e70"], // aqua
  ["#eda100", "#c98500"], // yellow
  ["#e87ba4", "#d55181"], // magenta
  ["#008300", "#008300"], // green
  ["#4a3aa7", "#9085e9"], // violet
  ["#e34948", "#e66767"], // red
];

function hashString(value: string): number {
  let hash = 5381;
  for (let i = 0; i < value.length; i++) {
    hash = (hash * 33) ^ value.charCodeAt(i);
  }
  return Math.abs(hash);
}

// Valeo's brand color is green, so it's pinned to that slot instead of
// whatever the hash happens to land on.
const SLOT_OVERRIDES: Record<string, number> = {
  valeo: 5, // green
};

export function companyColor(name: string): { light: string; dark: string } {
  const key = name.trim().toLowerCase();
  const index = key in SLOT_OVERRIDES ? SLOT_OVERRIDES[key] : hashString(key) % COMPANY_COLOR_SLOTS.length;
  const [light, dark] = COMPANY_COLOR_SLOTS[index];
  return { light, dark };
}
