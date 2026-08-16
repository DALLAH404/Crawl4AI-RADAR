import { companyColor } from "@/lib/companyColor";

// Shown in place of a photo when an article has no image_url — a colored
// initial-letter monogram using the same per-company color already used for
// badges/dots elsewhere. Background stays the neutral bg-muted regardless of
// company (not a solid fill of the brand color) so the letter's contrast
// never depends on which of the 8 categorical hues a given company lands on.
export function CompanyLogoFallback({ company }: { company: string }) {
  const { light, dark } = companyColor(company);
  const initial = company.trim().charAt(0).toUpperCase();

  return (
    <div className="flex size-full items-center justify-center bg-muted" aria-hidden="true">
      <span className="text-3xl font-bold dark:hidden" style={{ color: light }}>
        {initial}
      </span>
      <span className="hidden text-3xl font-bold dark:inline" style={{ color: dark }}>
        {initial}
      </span>
    </div>
  );
}
