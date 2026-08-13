"""Shared fixtures for radar_pipeline tests — moto-mocked AWS resources."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from radar_pipeline.db import connect, ensure_table

TEST_BUCKET = "test-radar-raw"


@pytest.fixture
def store():
    with mock_aws():
        s = connect(table_name="test-radar-articles", region_name="us-east-1")
        ensure_table(s)
        yield s


@pytest.fixture
def s3_client():
    """A moto-mocked S3 client with TEST_BUCKET already created — for tests
    that only need S3, not the DynamoDB store. Tests needing both should use
    the `store` fixture and create a bucket inline (mock_aws() context
    managers nest fine, but there's no need to stack two here)."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=TEST_BUCKET)
        yield client
