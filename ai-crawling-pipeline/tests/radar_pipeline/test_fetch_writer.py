"""Tests for fetch/writer.py — local disk and S3 output, same rendered content."""

from __future__ import annotations

from pathlib import Path

from radar_pipeline.fetch.writer import (
    make_slug,
    render_article_markdown,
    s3_key_for_run,
    upload_article_md,
    write_article_md,
)

TEST_BUCKET = "test-radar-raw"  # matches conftest.py's s3_client fixture


def _article_kwargs(**overrides):
    defaults = dict(
        article_hash="abcdef1234567890",
        source_id="bosch",
        title="Bosch launches a new brake pad line",
        url="https://example.com/bosch-brake-pads",
        markdown="Full article body here.",
        category="auto",
        tag="Bosch",
        product_line="Freios",
        event_type="Lancamento",
        alert_level="Alto",
        date="2026-08-13",
        image_url="https://example.com/img.jpg",
    )
    defaults.update(overrides)
    return defaults


class TestRenderArticleMarkdown:
    def test_front_matter_and_body(self):
        content = render_article_markdown(**_article_kwargs())
        assert content.startswith("---\n")
        assert "id_hash: abcdef1234567890" in content
        assert "tag: Bosch" in content
        assert "# Bosch launches a new brake pad line" in content
        assert "URL: https://example.com/bosch-brake-pads" in content
        assert content.endswith("Full article body here.")


class TestS3KeyForRun:
    def test_key_includes_prefix_run_and_source(self):
        key = s3_key_for_run(
            prefix="raw", run_timestamp="20260813T120000Z",
            source_id="bosch", article_hash="abcdef1234567890", title="Some title",
        )
        assert key.startswith("raw/20260813T120000Z/bosch/")
        assert key.endswith(".md")
        assert key == f"raw/20260813T120000Z/bosch/{make_slug('bosch', 'abcdef1234567890', 'Some title')}.md"


class TestWriteArticleMdLocal:
    def test_writes_expected_file(self, tmp_path: Path):
        out_path = write_article_md(output_dir=tmp_path, **_article_kwargs())
        assert out_path.exists()
        assert out_path.read_text(encoding="utf-8") == render_article_markdown(**_article_kwargs())


class TestUploadArticleMd:
    def test_uploads_to_expected_key_with_expected_content(self, s3_client):
        key = upload_article_md(
            s3_client, bucket=TEST_BUCKET, prefix="raw", run_timestamp="20260813T120000Z",
            **_article_kwargs(),
        )
        assert key.startswith("raw/20260813T120000Z/bosch/")

        obj = s3_client.get_object(Bucket=TEST_BUCKET, Key=key)
        body = obj["Body"].read().decode("utf-8")
        assert body == render_article_markdown(**_article_kwargs())
        assert obj["ContentType"].startswith("text/markdown")
