import Link from "next/link";
import { companyColor } from "@/lib/companyColor";

// Text is the identifier; the dot is a secondary scanning aid, never the
// sole carrier of identity (dataviz skill's categorical-color rule).
export function CompanyBadge({
  name,
  href,
}: {
  name: string;
  href?: string;
}) {
  const { light, dark } = companyColor(name);

  const content = (
    <>
      <span
        className="size-1.5 rounded-full dark:hidden"
        style={{ backgroundColor: light }}
        aria-hidden="true"
      />
      <span
        className="hidden size-1.5 rounded-full dark:inline-block"
        style={{ backgroundColor: dark }}
        aria-hidden="true"
      />
      {name}
    </>
  );

  const className =
    "inline-flex items-center gap-1 rounded-full border border-border bg-card px-1.5 py-0.5 text-[11px] font-medium text-card-foreground";

  if (href) {
    return (
      <Link href={href} className={`${className} hover:bg-accent hover:text-accent-foreground`}>
        {content}
      </Link>
    );
  }

  return <span className={className}>{content}</span>;
}
