import Image from "next/image";

export function Hero() {
  return (
    <section className="relative aspect-[1584/672] w-full overflow-hidden">
      <Image
        src="/hero.jpg"
        alt="A Valeo test facility with a radar sensing tower on the roof"
        fill
        priority
        sizes="100vw"
        className="object-cover"
      />

      <div className="absolute inset-0 bg-gradient-to-t from-background via-background/35 to-transparent" />

      <div className="absolute inset-x-0 bottom-0 p-6 md:p-10">
        <p className="font-display text-xs font-semibold uppercase tracking-widest text-primary">
          RADAR
        </p>
        <h1 className="mt-1 max-w-xl font-display text-2xl font-semibold leading-tight text-foreground drop-shadow-sm md:text-4xl">
          Every competitor move, one feed.
        </h1>
        <p className="mt-2 max-w-lg text-sm text-foreground/80 md:text-base">
          Scanning the automotive aftermarket for news and social activity, around the clock.
        </p>
      </div>
    </section>
  );
}
