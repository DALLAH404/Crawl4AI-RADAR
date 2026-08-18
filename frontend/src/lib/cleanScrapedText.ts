// LinkedIn post text (title / action_description — see lib/types.ts) is
// raw scraped markdown, not prose written for display: company/topic
// mentions and hashtags come through as literal `[text](url)` /
// `[#tag](url)` markdown links, tracking params and all. The AI-written
// `summary` never has this problem, so this is only needed as a fallback
// for when there's no summary to show instead (see ArticleDetail.tsx).
export function cleanScrapedText(text: string): string {
  return text
    // Hashtag links carry no meaning once separated from the URL — drop
    // the whole thing, text and link both, not just the link.
    .replace(/\[#[^\]]*\]\([^)]*\)/g, "")
    // Everything else in `[text](url)` form: keep the visible text (it's
    // usually a real mention, e.g. a company name), drop the tracking URL.
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    // Bare hashtags that weren't wrapped in a markdown link.
    .replace(/#\S+/g, "")
    // Bare URLs that weren't wrapped in a markdown link either.
    .replace(/https?:\/\/\S+/g, "")
    .replace(/[ \t]+/g, " ")
    .trim();
}
