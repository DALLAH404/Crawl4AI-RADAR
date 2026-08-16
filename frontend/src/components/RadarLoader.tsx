import styles from "./RadarLoader.module.css";

// Full-screen radar-console loading state, shown by Next.js in place of a
// route's page.tsx while its async data fetch is in flight (see the sibling
// loading.tsx files). The .loader mark itself is from Uiverse.io (mrhyddenn) —
// see RadarLoader.module.css.
export function RadarLoader() {
  return (
    <div className="flex min-h-screen flex-1 items-center justify-center bg-[#050806]">
      <div className={styles.loader}>
        <span className={styles.sweep} />
      </div>
    </div>
  );
}
