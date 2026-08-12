"""Migration runner for radar_pipeline schema versions."""

from importlib import import_module


def apply_migration(con, version: int) -> None:
    mod = import_module(f"radar_pipeline.migrations.migration_{version:04d}")
    mod.apply(con)
