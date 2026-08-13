# RADAR — Deployment Guide

Every step in this file that touches AWS is something **you** run — from CloudShell,
your own machine, or the console — not something done from this Codespace (it has no
AWS credentials attached). Each step says what to check afterward to confirm it
worked. Code, configs, and Dockerfiles are committed to the repo as they're written;
this file is the order to run the AWS-side steps in.

Work through phases in order. Phase 1 (this section) covers the DynamoDB table the
pipeline now writes to.

---

## Phase 1 — DynamoDB table

The pipeline code (`ai-crawling-pipeline/src/radar_pipeline/db.py`) expects one table
with four Global Secondary Indexes. The schema and the reasoning behind it are
documented in `ai-crawling-pipeline/src/radar_pipeline/README.md` under
"Database layer — db.py" — this section is just the steps to create it for real.

The application itself never creates this table against real AWS (`db.ensure_table()`
is guarded to only run when `db.endpoint_url` is set, i.e. local/dev only) — so the
task's IAM role can stay read/write-only with no `CreateTable` permission. You create
it once, here.

### Option A — AWS Console

1. Open the **DynamoDB** console → **Tables** → **Create table**.
2. **Table name**: `radar-articles` (or your own choice — just make sure it matches
   `db.table_name` in `configs/radar.yaml`, or set the `RADAR_TABLE_NAME` env var to
   override it later without touching the config file).
3. **Partition key**: `pk` (String). **Sort key**: `sk` (String).
4. **Table settings**: choose **Customize**, then under **Read/write capacity
   settings** choose **On-demand** (this workload runs in bursts every 3 hours, not
   continuously — provisioned capacity would mean paying for idle capacity most of
   the time, or throttling during the burst; on-demand matches the traffic shape).
5. Leave encryption at the default (AWS owned key) unless your org requires a
   customer-managed KMS key.
6. Click **Create table**. Wait for **Status: Active** (should take under a minute).
7. Once active, open the table → **Indexes** tab → **Create index**, four times, with
   these exact settings (all on-demand, same as the table; **Projected attributes**:
   **All**):

   | Index name | Partition key | Sort key |
   |---|---|---|
   | `CompanyTimeIndex` | `gsi1pk` (String) | `gsi1sk` (String) |
   | `LatestIndex` | `gsi2pk` (String) | `gsi2sk` (String) |
   | `DedupIndex` | `gsi3pk` (String) | `gsi3sk` (String) |
   | `PendingIndex` | `gsi4pk` (String) | `gsi4sk` (String) |

   Each index takes a few minutes to backfill (there's no data yet, so this should be
   near-instant) — wait for **Status: Active** on each before creating the next.

**Confirm it worked**: the table's **Indexes** tab lists all four by exactly those
names, each **Active**. `aws dynamodb describe-table --table-name radar-articles
--query 'Table.GlobalSecondaryIndexes[].IndexName'` (see below) should print all four.

### Option B — AWS CLI (CloudShell or your own machine)

Replace `radar-articles` if you chose a different table name, and `us-east-1` with
your region.

```bash
aws dynamodb create-table \
  --table-name radar-articles \
  --billing-mode PAY_PER_REQUEST \
  --attribute-definitions \
      AttributeName=pk,AttributeType=S \
      AttributeName=sk,AttributeType=S \
      AttributeName=gsi1pk,AttributeType=S \
      AttributeName=gsi1sk,AttributeType=S \
      AttributeName=gsi2pk,AttributeType=S \
      AttributeName=gsi2sk,AttributeType=S \
      AttributeName=gsi3pk,AttributeType=S \
      AttributeName=gsi3sk,AttributeType=S \
      AttributeName=gsi4pk,AttributeType=S \
      AttributeName=gsi4sk,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH AttributeName=sk,KeyType=RANGE \
  --global-secondary-indexes \
      '[
        {"IndexName":"CompanyTimeIndex","KeySchema":[{"AttributeName":"gsi1pk","KeyType":"HASH"},{"AttributeName":"gsi1sk","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}},
        {"IndexName":"LatestIndex","KeySchema":[{"AttributeName":"gsi2pk","KeyType":"HASH"},{"AttributeName":"gsi2sk","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}},
        {"IndexName":"DedupIndex","KeySchema":[{"AttributeName":"gsi3pk","KeyType":"HASH"},{"AttributeName":"gsi3sk","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}},
        {"IndexName":"PendingIndex","KeySchema":[{"AttributeName":"gsi4pk","KeyType":"HASH"},{"AttributeName":"gsi4sk","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}}
      ]' \
  --region us-east-1
```

Wait for it to become active:

```bash
aws dynamodb wait table-exists --table-name radar-articles --region us-east-1
```

**Confirm it worked**:

```bash
aws dynamodb describe-table --table-name radar-articles --region us-east-1 \
  --query 'Table.{Status:TableStatus,GSIs:GlobalSecondaryIndexes[].{Name:IndexName,Status:IndexStatus}}'
```

Expect `Status: ACTIVE` and all four GSIs listed with `Status: ACTIVE`.

### Optional — a separate dev/test table

If you want to run `scripts/check_dynamodb_idempotency.py` or a manual backfill
against something other than production, repeat either option above with a different
`--table-name` (e.g. `radar-articles-dev`), then point at it with
`--table-name radar-articles-dev` on that script, or `db.table_name` /
`RADAR_TABLE_NAME` for the pipeline itself.

### What this does *not* need yet

No IAM role, no credentials wiring, no application deployment happens in this phase —
that's Phase 3 (task execution/task role) and Phase 4 (scheduling). This phase only
creates the empty table the rest of the pipeline will read and write once it's
running somewhere with AWS access.

---

## Phase 2 — Containerize

The image is built from `ai-crawling-pipeline/` (that's the Docker build context —
run these commands from that directory). It bundles both `radar_pipeline` and its
sibling `ai_crawling_pipeline` package (they share one Python distribution), Playwright
+ Chromium (via the `mcr.microsoft.com/playwright/python` base image, pinned to the
same Playwright version the project depends on), and everything in `requirements.txt`
(exported from `uv.lock`, so it matches exactly what the test suite runs against).

`radar-pipeline` needs **`AWS_DEFAULT_REGION`** set explicitly wherever it runs —
confirmed while testing this image locally: this botocore version does not resolve a
region from `AWS_REGION` alone the way Lambda's runtime does, only from
`AWS_DEFAULT_REGION` or an explicit `region_name`. Don't forget this in the Phase 3
task definition's environment variables, or every DynamoDB call will fail with
`NoRegionError` before it even gets to check credentials.

### Test the image locally (no AWS needed for this part)

```bash
cd ai-crawling-pipeline
docker build -t radar-pipeline:local .

# Sanity check that doesn't touch AWS at all — just parses the bundled YAML
# source catalog:
docker run --rm --entrypoint radar-pipeline radar-pipeline:local sources-list --active-only

# Confirms the container reaches AWS correctly (expect it to fail on
# UnrecognizedClientException with these fake credentials — that's the
# correct failure; it means region + request signing worked and only the
# credentials themselves are invalid, which real ones from the Phase 3 task
# role will fix):
docker run --rm --entrypoint radar-pipeline \
  -e AWS_DEFAULT_REGION=us-east-1 -e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test \
  radar-pipeline:local --status
```

To actually run it against your real DynamoDB table from here, swap in real
credentials (e.g. `-e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... -e
AWS_SESSION_TOKEN=...` from `aws configure export-credentials` or similar) and a real
`OPENAI_API_KEY` for `--summarize`.

### Create the ECR repo

**Console**: ECR → **Repositories** → **Create repository**.
- **Visibility**: Private.
- **Repository name**: `news-scraper`.
- Leave **Tag immutability** and **Scan on push** at their defaults (or turn on
  **Scan on push** if you want vulnerability scanning on every push — optional, no
  cost impact on the pipeline itself).
- Everything else default. **Create repository**.

**Confirm it worked**: the repository list shows `news-scraper`; `aws ecr
describe-repositories --repository-names news-scraper --region <your-region>` returns
its `repositoryUri`.

**CLI equivalent**:

```bash
aws ecr create-repository --repository-name news-scraper --region <your-region>
```

### Push the image

`push_to_ecr.sh` (in `ai-crawling-pipeline/`) builds, authenticates, tags, and pushes.
Run it yourself from somewhere with AWS access — CloudShell or your own machine, not
this Codespace:

```bash
cd ai-crawling-pipeline
AWS_ACCOUNT_ID=<your-account-id> AWS_REGION=<your-region> ./push_to_ecr.sh
```

**Confirm it worked**: the script prints the pushed image URI at the end
(`<account>.dkr.ecr.<region>.amazonaws.com/news-scraper:latest`); `aws ecr
list-images --repository-name news-scraper --region <your-region>` should list a
`latest`-tagged image. Keep the printed URI — Phase 3's task definition needs it.

---

*(Later phases — task definition/IAM, scheduling, verification, read API, frontend —
will each add their own section here as they're built.)*
