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

*(Later phases — containerize, task definition/IAM, scheduling, verification, read
API, frontend — will each add their own section here as they're built.)*
