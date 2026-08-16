import { STOCK_TICKERS } from "@/lib/stockTickers";

// Server-only key — never exposed to the browser. StockTicker.tsx polls this
// route rather than calling Twelve Data directly.
const API_KEY = process.env.TWELVE_DATA_API_KEY;

// Matches the client's own poll interval (see StockTicker.tsx) — the
// underlying Twelve Data fetch is shared/cached across every visitor via
// Next's Data Cache, so N browser tabs polling this route still only spends
// one batch of API credits per interval, not N.
const REVALIDATE_SECONDS = 60 * 60;

export interface StockQuote {
  company: string;
  symbol: string;
  price: number;
  changePercent: number;
  currency: string;
}

// Confirmed against a live batch call: for multiple symbols, Twelve Data
// returns an object keyed by the EXACT string you requested (e.g.
// "BWA:NYSE"), not by the quote's own bare `symbol` field ("BWA") — so that
// outer key, not `quote.symbol`, is what must be matched back to a company.
// A single-symbol request instead returns one bare quote object with no
// wrapping key, handled as a fallback below (unused in practice since GET
// always batches, but cheap to keep correct).
function normalizeQuotes(data: unknown): { requestedSymbol: string; quote: Record<string, unknown> }[] {
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const obj = data as Record<string, unknown>;
    if (typeof obj.symbol === "string") {
      return [{ requestedSymbol: obj.symbol, quote: obj }];
    }
    return Object.entries(obj).flatMap(([requestedSymbol, value]) => {
      if (typeof value !== "object" || value === null) return [];
      const quote = value as Record<string, unknown>;
      if (quote.status === "error") return [];
      return [{ requestedSymbol, quote }];
    });
  }
  return [];
}

export async function GET() {
  if (!API_KEY) {
    console.error("TWELVE_DATA_API_KEY is not set — /api/stocks returning no items");
    return Response.json({ items: [] satisfies StockQuote[] });
  }

  const symbolToCompany = new Map(
    Object.entries(STOCK_TICKERS).map(([company, symbol]) => [symbol, company]),
  );
  const symbols = [...symbolToCompany.keys()];

  const url = `https://api.twelvedata.com/quote?symbol=${encodeURIComponent(symbols.join(","))}&apikey=${API_KEY}`;

  let data: unknown;
  try {
    const res = await fetch(url, { next: { revalidate: REVALIDATE_SECONDS } });
    data = await res.json();
  } catch (error) {
    console.error("Twelve Data request failed:", error);
    return Response.json({ items: [] satisfies StockQuote[] });
  }

  const items: StockQuote[] = normalizeQuotes(data).flatMap(({ requestedSymbol, quote }) => {
    const company = symbolToCompany.get(requestedSymbol);
    const price = Number(quote.close);
    const changePercent = Number(quote.percent_change);

    if (!company || Number.isNaN(price) || Number.isNaN(changePercent)) {
      return [];
    }

    return [
      {
        company,
        symbol: requestedSymbol,
        price,
        changePercent,
        currency: typeof quote.currency === "string" ? quote.currency : "",
      },
    ];
  });

  return Response.json({ items });
}
