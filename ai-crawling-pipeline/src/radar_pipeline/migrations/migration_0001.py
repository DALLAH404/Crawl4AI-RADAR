"""Migration 0001 — Initial schema.

Creates all tables, indexes, and views for the Radar Aftermarket Pipeline.
This is applied automatically by init_schema() on first run.
"""


def apply(con) -> None:
    from radar_pipeline.db import init_schema

    init_schema(con, dim=768)
