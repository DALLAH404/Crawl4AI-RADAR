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
- **Repository name**: `radar-scraper`.
- Leave **Tag immutability** and **Scan on push** at their defaults (or turn on
  **Scan on push** if you want vulnerability scanning on every push — optional, no
  cost impact on the pipeline itself).
- Everything else default. **Create repository**.

**Confirm it worked**: the repository list shows `radar-scraper`; `aws ecr
describe-repositories --repository-names radar-scraper --region <your-region>` returns
its `repositoryUri`.

**CLI equivalent**:

```bash
aws ecr create-repository --repository-name radar-scraper --region <your-region>
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
(`<account>.dkr.ecr.<region>.amazonaws.com/radar-scraper:latest`); `aws ecr
list-images --repository-name radar-scraper --region <your-region>` should list a
`latest`-tagged image. Keep the printed URI — Phase 3's task definition needs it.

---

## Phase 3 — Task definition and IAM

Four things need to exist before the container can run on Fargate: the summarize
stage's LLM API key stored as a parameter (not a plaintext env var), the two IAM
roles the task definition references, the ECS cluster, and the registered task
definition itself. Do these in order — each one after the first references the
previous.

Everywhere below, replace `<ACCOUNT_ID>` and `<REGION>` with your account ID and
region (`aws sts get-caller-identity --query Account --output text` gets the
account ID from CloudShell). `task-definition.json`, `iam/task-execution-policy.json`,
and `iam/task-role-policy.json` all have the same placeholders — edit them in place,
or substitute on the fly with `sed` as shown below.

### 1. Store the summarize LLM API key

`configs/radar.yaml`'s `summarize.llm` points at an OpenAI-compatible endpoint and
reads its key from `OPENAI_API_KEY` (`summarize.llm.api_key_env`). This goes in SSM
Parameter Store as a `SecureString` — never as a plaintext environment variable in
the task definition.

**Console**: Systems Manager → **Parameter Store** → **Create parameter**.
- **Name**: `/radar-scraper/openai-api-key`
- **Tier**: Standard.
- **Type**: **SecureString** (leave the default AWS-managed KMS key unless your org
  requires a customer-managed one).
- **Value**: your actual API key for whatever endpoint `summarize.llm.base_url`
  points at (the sample config uses an OpenCode Zen endpoint, not literally OpenAI —
  the env var is just named `OPENAI_API_KEY` because that's the default the
  OpenAI-compatible client library looks for).
- **Create parameter**.

**Confirm it worked**: the parameter list shows `/radar-scraper/openai-api-key` with
**Type: SecureString**.

**CLI equivalent**:

```bash
aws ssm put-parameter \
  --name /radar-scraper/openai-api-key \
  --type SecureString \
  --value "<your-api-key>" \
  --region <REGION>
```

If dedup's Layer 3/4 (Gemini embedding + LLM judge) ever gets re-enabled — it's
currently dead code, see `src/radar_pipeline/README.md` — repeat this for
`GEMINI_API_KEY` under a parallel parameter name and add it to both
`task-execution-policy.json`'s resource list and the task definition's `secrets`.
Not needed for the pipeline as it runs today.

### 2. Create the IAM roles

Two roles, two purposes — keep them separate (least privilege in both directions,
same principle as the DynamoDB scoping in Phase 1):

- **Execution role** — what ECS itself needs *before* your code runs: pull the image
  from ECR, create the CloudWatch log stream, and resolve the SSM parameter into an
  environment variable inside the container. Uses `iam/task-execution-policy.json`.
- **Task role** — what your code needs *while* running: DynamoDB read/write on
  `radar-articles` and its GSIs only. Uses `iam/task-role-policy.json`.

Fill in the placeholders first:

```bash
cd ai-crawling-pipeline
sed -i "s/<ACCOUNT_ID>/<your-account-id>/g; s/<REGION>/<your-region>/g" \
  iam/task-execution-policy.json iam/task-role-policy.json task-definition.json
```

**Console** (repeat once per policy):

1. IAM → **Policies** → **Create policy** → **JSON** tab → paste the contents of
   `iam/task-execution-policy.json`. **Next** → name it
   `radar-scraper-execution-policy` → **Create policy**.
2. Repeat for `iam/task-role-policy.json`, named `radar-scraper-task-policy`.
3. IAM → **Roles** → **Create role**.
   - **Trusted entity type**: AWS service.
   - **Use case**: **Elastic Container Service** → **Elastic Container Service Task**
     (this sets the trust policy to the `ecs-tasks.amazonaws.com` service principal,
     which both roles need — it's what lets ECS itself assume them on the task's
     behalf).
   - **Permissions**: attach `radar-scraper-execution-policy`.
   - **Role name**: `radar-scraper-execution-role`. **Create role**.
4. Repeat step 3 for the task role: attach `radar-scraper-task-policy`, name it
   `radar-scraper-task-role`.

**Confirm it worked**: IAM → Roles shows both `radar-scraper-execution-role` and
`radar-scraper-task-role`, each with **Trusted entities: ecs-tasks.amazonaws.com**
and exactly one attached policy.

**CLI equivalent**:

```bash
TRUST_POLICY='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

aws iam create-role --role-name radar-scraper-execution-role \
  --assume-role-policy-document "$TRUST_POLICY" --region <REGION>
aws iam create-role --role-name radar-scraper-task-role \
  --assume-role-policy-document "$TRUST_POLICY" --region <REGION>

aws iam put-role-policy --role-name radar-scraper-execution-role \
  --policy-name radar-scraper-execution-policy \
  --policy-document file://iam/task-execution-policy.json
aws iam put-role-policy --role-name radar-scraper-task-role \
  --policy-name radar-scraper-task-policy \
  --policy-document file://iam/task-role-policy.json
```

### 3. Create the ECS cluster

**Console**: ECS → **Clusters** → **Create cluster**.
- **Cluster name**: `radar-scraper`.
- **Infrastructure**: **AWS Fargate (serverless)** only — leave the Amazon EC2
  Instances and EC2 Auto Scaling Group boxes unchecked; this workload never needs a
  managed EC2 capacity provider.
- **Create**.

**Confirm it worked**: the cluster list shows `radar-scraper` with **Status: Active**
and (once Phase 4/5 actually runs a task) an Fargate infrastructure entry.

**CLI equivalent**:

```bash
aws ecs create-cluster --cluster-name radar-scraper --region <REGION>
```

### 4. Register the task definition

`task-definition.json` (repo root of `ai-crawling-pipeline/`) has the placeholders
already filled in from step 2 above. Sizing note: `cpu: 2048` (2 vCPU) / `memory:
8192` (8 GB) is a starting point sized for Crawl4AI running up to 4 concurrent
headless Chromium instances (the fetch stage's default concurrency) plus Python/boto3
overhead — headless Chromium is memory-hungry, and running out of memory kills the
task outright rather than degrading gracefully. Watch actual CloudWatch memory
utilization after a few real runs (Phase 5) and size down if it's consistently
over-provisioned; Fargate bills for what's reserved, not just what's used.

**Console**: ECS → **Task definitions** → **Create new task definition** → **JSON**
tab (top right) → replace the entire editor contents with `task-definition.json` →
**Create**.

**Confirm it worked**: the task definition list shows `radar-scraper` at revision 1;
opening it shows both role ARNs resolved (not placeholder text) and the
`radar-scraper` container with its log configuration.

**CLI equivalent**:

```bash
aws ecs register-task-definition --cli-input-json file://task-definition.json --region <REGION>
```

Confirm: `aws ecs describe-task-definition --task-definition radar-scraper --region
<REGION> --query 'taskDefinition.{Status:status,Revision:revision}'` returns
`ACTIVE` at revision 1.

---

## Phase 4 — Scheduling

An EventBridge Scheduler schedule fires every 3 hours and calls ECS `RunTask` with
the Phase 3 task definition. Two things need to exist first: a security group giving
the task outbound internet access (the scraper reaches external RSS feeds, Google
News, LinkedIn, and the summarize LLM endpoint — none of that is optional), and an
IAM role letting EventBridge Scheduler actually call `RunTask` on your behalf.

### 1. Networking — subnets and security group

Fargate tasks in `awsvpc` mode need explicit subnets and a security group at
`RunTask` time; there's no default. The simplest correct setup for this workload —
no inbound traffic ever, only outbound — is **public subnets with an
auto-assigned public IP**, which needs no NAT Gateway (that's a real cost saver: a
NAT Gateway runs ~$32/month plus data processing charges, for a task that only needs
to *initiate* outbound connections, never receive inbound ones). Private subnets +
NAT is the more locked-down alternative if your org requires it — same target
config below, just point `subnets` at private subnets that have a NAT Gateway route.

**Console** — create the security group:

1. VPC → **Security groups** → **Create security group**.
2. **Name**: `radar-scraper-sg`. **VPC**: your default VPC (or whichever VPC you're
   deploying into).
3. **Inbound rules**: none — leave empty. The task never listens for connections.
4. **Outbound rules**: the default outbound-all-traffic rule that new security
   groups start with is fine here (`0.0.0.0/0`, all traffic) — RSS feeds, Google
   News, LinkedIn, DynamoDB, ECR, CloudWatch, SSM, and whatever endpoint
   `summarize.llm.base_url` points at are all reached over HTTPS, but feed URLs are
   occasionally plain HTTP, so restricting to 443 alone can silently break a source.
   If you want it tighter than "all traffic," allow **443/tcp** and **80/tcp** to
   `0.0.0.0/0` specifically and watch `--validate-feeds` / CloudWatch logs for
   anything that still fails to connect.
5. **Create security group**. Note the security group ID (`sg-...`).

**Find your subnet IDs**: VPC → **Subnets** → filter by your VPC → note two subnet
IDs in different Availability Zones that have **Auto-assign public IPv4 address**
enabled (public subnets in the default VPC have this on already). If none do, edit
one: select it → **Actions** → **Edit subnet settings** → enable auto-assign public
IPv4.

**Confirm it worked**: `aws ec2 describe-security-groups --group-ids <sg-id>
--query 'SecurityGroups[0].{Inbound:IpPermissions,Outbound:IpPermissionsEgress}'`
shows an empty inbound list and a non-empty outbound list.

### 2. IAM role for EventBridge Scheduler

`iam/eventbridge-scheduler-policy.json` grants exactly `ecs:RunTask` (scoped to the
`radar-scraper` task definition and cluster only) and `iam:PassRole` for the two
Phase 3 task roles (scoped further with an `iam:PassedToService` condition, so this
role can't be used to pass those roles to anything other than ECS tasks).

Fill in the placeholders the same way as Phase 3:

```bash
cd ai-crawling-pipeline
sed -i "s/<ACCOUNT_ID>/<your-account-id>/g; s/<REGION>/<your-region>/g" \
  iam/eventbridge-scheduler-policy.json
```

**Console**:

1. IAM → **Policies** → **Create policy** → **JSON** tab → paste
   `iam/eventbridge-scheduler-policy.json` → name it
   `radar-scraper-scheduler-policy` → **Create policy**.
2. IAM → **Roles** → **Create role** → **Trusted entity type**: Custom trust policy
   → paste:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": { "Service": "scheduler.amazonaws.com" },
       "Action": "sts:AssumeRole"
     }]
   }
   ```
3. **Permissions**: attach `radar-scraper-scheduler-policy`.
4. **Role name**: `radar-scraper-scheduler-role`. **Create role**.

**Shortcut**: EventBridge Scheduler's console can auto-create a correctly-scoped role
for you at the point where you attach the ECS target in step 3 below ("Create new
role for this schedule"). That's the faster path if you're only ever going to have
this one schedule; creating the role up front as above is better if you want the
role's permissions in version control (this repo) rather than console-generated and
undocumented.

**Confirm it worked**: IAM → Roles → `radar-scraper-scheduler-role` shows **Trusted
entities: scheduler.amazonaws.com** and the attached policy.

### 3. Create the schedule

**Console**: EventBridge → **Scheduler** → **Create schedule**.

1. **Schedule name**: `radar-scraper-every-3-hours`.
2. **Schedule pattern**: **Recurring schedule**. Either works — pick one:
   - **Rate-based**: `rate(3 hours)` — simplest, fires every 3 hours from whenever
     the schedule was created/enabled.
   - **Cron-based**: `cron(0 */3 * * ? *)` — fires at fixed wall-clock hours
     (00:00, 03:00, 06:00, ... UTC). Slightly more predictable for correlating with
     CloudWatch log timestamps (also UTC) and matches `collect.hours_back: 3`'s
     assumption of clean, non-overlapping windows.
3. **Flexible time window**: **Off** — for a single schedule there's no thundering
   herd to spread out, and predictable timing makes the `hours_back` window easier
   to reason about.
4. **Target**: **Templated targets** → **ECS Task**.
   - **Cluster**: `radar-scraper`.
   - **Task definition**: `radar-scraper` (latest revision).
   - **Launch type**: **FARGATE**. **Platform version**: `LATEST`.
   - **Task count**: `1`.
   - **Subnets**: the two public subnet IDs from step 1.
   - **Security group**: `radar-scraper-sg`.
   - **Auto-assign public IP**: **Enabled** (required for outbound internet access
     from a public subnet with no NAT Gateway).
5. **Permissions**: choose the existing role `radar-scraper-scheduler-role` (or let
   the console create one, per the shortcut above).
6. **Create schedule**.

**Confirm it worked**: EventBridge → Scheduler → `radar-scraper-every-3-hours` shows
**State: Enabled** and **Next invocation** populated a few hours out. Phase 5 covers
actually triggering one manually and checking the result end to end.

**CLI equivalent** (cron version; swap the `ScheduleExpression` for the rate form if
preferred):

```bash
aws scheduler create-schedule \
  --name radar-scraper-every-3-hours \
  --schedule-expression "cron(0 */3 * * ? *)" \
  --flexible-time-window '{"Mode": "OFF"}' \
  --target '{
    "Arn": "arn:aws:ecs:<REGION>:<ACCOUNT_ID>:cluster/radar-scraper",
    "RoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/radar-scraper-scheduler-role",
    "EcsParameters": {
      "TaskDefinitionArn": "arn:aws:ecs:<REGION>:<ACCOUNT_ID>:task-definition/radar-scraper",
      "LaunchType": "FARGATE",
      "PlatformVersion": "LATEST",
      "TaskCount": 1,
      "NetworkConfiguration": {
        "awsvpcConfiguration": {
          "Subnets": ["<subnet-id-1>", "<subnet-id-2>"],
          "SecurityGroups": ["<sg-id>"],
          "AssignPublicIp": "ENABLED"
        }
      }
    }
  }' \
  --region <REGION>
```

Confirm: `aws scheduler get-schedule --name radar-scraper-every-3-hours --region
<REGION> --query '{State:State,Next:ScheduleExpression}'`.

---

*(Later phases — verification, read API, frontend — will each add their own section
here as they're built.)*
