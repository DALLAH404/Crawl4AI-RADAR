"""Shared fixtures for radar_pipeline tests — a moto-mocked DynamoDB table."""

from __future__ import annotations

import pytest
from moto import mock_aws

from radar_pipeline.db import connect, ensure_table


@pytest.fixture
def store():
    with mock_aws():
        s = connect(table_name="test-radar-articles", region_name="us-east-1")
        ensure_table(s)
        yield s
