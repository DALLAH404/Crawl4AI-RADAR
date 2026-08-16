"use client";

import { useEffect, useState } from "react";

// Mirrors the StockQuote shape from app/api/stocks/route.ts — kept as a
// separate client-side type rather than importing the route module directly,
// same tradeoff as lib/types.ts vs the read-api Lambda.
interface Quote {
  company: string;
  symbol: string;
  price: number;
  changePercent: number;
  currency: string;
}

const POLL_MS = 60 * 60_000;

export function StockTicker() {
  const [quotes, setQuotes] = useState<Quote[]>([]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const res = await fetch("/api/stocks");
        const data = await res.json();
        if (!cancelled && Array.isArray(data.items)) {
          setQuotes(data.items);
        }
      } catch {
        // Silently skip a bad poll — the ticker just keeps showing the last
        // good data until the next interval.
      }
    }

    load();
    const interval = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (quotes.length === 0) return null;

  // Duplicated so the CSS animation can loop seamlessly from 0% to -50%.
  const track = [...quotes, ...quotes];

  return (
    <div className="overflow-hidden border-b border-border bg-muted/50">
      <div className="flex w-max animate-[ticker-scroll_45s_linear_infinite] gap-8 py-1.5 hover:[animation-play-state:paused]">
        {track.map((quote, i) => (
          <QuoteItem key={`${quote.symbol}-${i}`} quote={quote} />
        ))}
      </div>
    </div>
  );
}

function QuoteItem({ quote }: { quote: Quote }) {
  const isUp = quote.changePercent > 0;
  const isDown = quote.changePercent < 0;

  return (
    <span className="flex items-center gap-1.5 whitespace-nowrap px-2 text-xs">
      <span className="font-semibold text-foreground">{quote.company}</span>
      <span className="text-muted-foreground">
        {quote.price.toFixed(2)} {quote.currency}
      </span>
      <span
        className={
          "flex items-center gap-0.5 font-medium " +
          (isUp ? "text-primary" : isDown ? "text-destructive" : "text-muted-foreground")
        }
      >
        {isUp && "▲"}
        {isDown && "▼"}
        {Math.abs(quote.changePercent).toFixed(2)}%
      </span>
    </span>
  );
}
