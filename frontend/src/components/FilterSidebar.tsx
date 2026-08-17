import { CompanyFilter } from "./CompanyFilter";
import { KindFilter } from "./KindFilter";
import { DateRangeCalendar } from "./DateRangeCalendar";

export function FilterSidebar() {
  return (
    <aside className="w-full lg:w-64 lg:shrink-0 lg:sticky lg:top-16 lg:self-start">
      <div className="flex flex-col divide-y divide-border rounded-lg bg-card shadow-lg shadow-gray-500/20">
        <div className="p-4">
          <KindFilter />
        </div>
        <div className="p-4">
          <DateRangeCalendar />
        </div>
        <div className="p-4">
          <CompanyFilter />
        </div>
      </div>
    </aside>
  );
}
