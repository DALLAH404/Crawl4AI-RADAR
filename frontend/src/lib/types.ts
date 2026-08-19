// Mirrors read-api/lambda_function.py's _public_article() shape exactly —
// see read-api/README.md for the source of truth. Kept as a hand-maintained
// type rather than generated, same tradeoff the Lambda itself makes against
// importing radar_pipeline.db: this frontend has no build-time access to the
// Python source, so the two are duplicated on purpose and need updating by
// hand together.
export type ContentKind = "news" | "social";

export interface Article {
  article_hash: string;
  title: string;
  link: string;
  image_url: string;
  // Full ordered image list — more than one entry only for a LinkedIn
  // carousel post. The feed card still only shows image_url (the first
  // one); ArticleDetail is what shows the rest.
  image_urls: string[];
  summary: string;
  // Full scraped post text — only meaningfully "full" for social (LinkedIn)
  // sources; for news sources it's just a short snippet, no more complete
  // than `summary`. May be missing/empty on older articles reached via a
  // company filter (read-api/lambda_function.py's _public_article comment
  // has the why).
  action_description: string;
  competitor_analysis: string;
  category: string;
  content_kind: ContentKind;
  event_type: string;
  alert_level: string;
  summary_status: string;
  published_at: string;
  companies: string[];
  source_name: string;
}

export interface ArticlesResponse {
  items: Article[];
  next_cursor: string | null;
}
