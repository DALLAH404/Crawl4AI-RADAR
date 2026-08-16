// Maps a tracked company tag (see companies.ts / radar_sources.yaml) to its
// real, currently-listed Twelve Data symbol — each verified against Twelve
// Data's own /symbol_search AND a live /quote call, not guessed, since a
// wrong ticker would silently show the wrong company's price.
//
// The free plan turned out to only serve US-exchange symbols — every
// European/Asian/Brazilian symbol tried (Euronext, XETR, JPX, Bovespa, even
// Stockholm's OMX) came back 403 "not available with your plan," confirmed
// via a live call, not assumed. That knocks out every non-US name that
// would otherwise have been included: Valeo (FR:Euronext), Continental
// (CON:XETR), Schaeffler (SHA0:XETR), SKF (SKF.B:OMX), Denso (6902:JPX),
// Randon (RAPT4:Bovespa), Fras-le (FRAS3:Bovespa) — all individually
// confirmed to resolve on /symbol_search, just not fetchable on this plan.
// Philips survives only because its NYSE-listed ADR (PHG) is a plain US
// listing, not because Euronext Amsterdam's PHIA became available.
//
// Also excluded, for unrelated reasons:
// - Bosch, ZF, MAHLE, Marelli, MANN+HUMMEL — foundation/PE-owned, no public
//   stock exists at all.
// - Tenneco — went private (Apollo Global Management, Nov 2022), delisted.
// - Hella — absorbed into Forvia (ex-Faurecia) via a 2022-23 squeeze-out, no
//   longer independently listed.
// - Osram — folded into ams-OSRAM; excluded rather than guessing which of the
//   resulting entities (and which exchange) the "Osram" tag should point to.
// - Anfavea, Automec, BCB, Câmbio, DPK, Fenabrave, IA, IBGE, Mercado,
//   Sindipeças, Sindirepa, Tecnologia — industry/market/association tags,
//   not companies; no ticker can exist.
// - Andap, Arteb, Dayco, Exedy, Fortbras, Laguna, Nakata, Pellegrino, Sabó,
//   Shocklight, SK Automotive, Tecfil, Trico, Wega — not individually
//   verified; treated as unlisted for now.
export const STOCK_TICKERS: Record<string, string> = {
  BorgWarner: "BWA:NYSE",
  GPC: "GPC:NYSE",
  Eaton: "ETN:NYSE",
  Gates: "GTES:NYSE",
  Philips: "PHG:NYSE",
};
