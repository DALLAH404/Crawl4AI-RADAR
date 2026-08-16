import { CompanyFilter } from "./CompanyFilter";
import { KindFilter } from "./KindFilter";
import { DateRangeCalendar } from "./DateRangeCalendar";

export function FilterSidebar() {
  return (
    <aside className="flex w-full flex-col gap-5 lg:w-64 lg:shrink-0 lg:sticky lg:top-16 lg:self-start">
      <KindFilter />
      <DateRangeCalendar />
      <CompanyFilter />
    </aside>
  );
}
