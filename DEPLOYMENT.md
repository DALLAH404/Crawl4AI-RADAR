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
   `0.0.0.0/0` specifically — and also **53/tcp+udp** (DNS) to `0.0.0.0/0`, or
   nothing will resolve at all (Amazon's VPC-provided DNS resolver is still subject
   to the security group's egress rules; it's easy to restrict to "web ports" and
   forget DNS isn't one of them). Watch `--validate-feeds` / CloudWatch logs for
   anything that still fails to connect either way.
5. **Create security group**. Note the security group ID (`sg-...`).

**Find your subnet IDs, and confirm they're actually public — two separate things**:
VPC → **Subnets** → filter by your VPC → note two subnet IDs in different
Availability Zones that have **Auto-assign public IPv4 address** enabled. That
setting alone is *not* sufficient — it only controls whether the task's network
interface gets a public IP attached, not whether that IP can reach anywhere. For
each candidate subnet, also check its **Route table** tab (or VPC → **Route Tables**
→ find the one associated with that subnet) for a `0.0.0.0/0` route targeting an
**Internet Gateway** (`igw-...`, not a NAT Gateway, and not absent). A subnet that
auto-assigns a public IP but routes through a NAT Gateway (or has no default route
at all) is a private subnet by the definition that matters here — the task will
start, get a public IP, and then time out trying to reach anything, including SSM
for its own secrets (see the Phase 5 troubleshooting note below for exactly this
failure). Public subnets in the *default* VPC have both properties already; a
custom VPC needs checking, and possibly creating/attaching an Internet Gateway and
adding the route yourself if it's missing.

**Confirm it worked**: `aws ec2 describe-security-groups --group-ids <sg-id>
--query 'SecurityGroups[0].{Inbound:IpPermissions,Outbound:IpPermissionsEgress}'`
shows an empty inbound list and a non-empty outbound list. For the subnet/route
table: `aws ec2 describe-route-tables --filters "Name=association.subnet-id,Values=<subnet-id>" --query 'RouteTables[0].Routes'`
should include a row with `DestinationCidrBlock: 0.0.0.0/0` and a `GatewayId`
starting `igw-`.

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

## Phase 5 — Verify end to end

Everything from Phases 1–4 exists now but has never actually run together. This
phase triggers exactly one Fargate run by hand (no need to wait for the schedule)
and confirms the whole chain: container starts → reaches the internet → writes to
DynamoDB → logs land in CloudWatch.

### 1. Trigger one run manually

**Console**: ECS → **Clusters** → `radar-scraper` → **Tasks** tab → **Run new task**.

- **Compute options**: **Launch type** → **FARGATE**.
- **Application type**: **Task**.
- **Family**: `radar-scraper`, **Revision**: latest (should be `1` unless you've
  re-registered it since).
- **Desired tasks**: `1`.
- **Networking**: same VPC as Phase 4 → **Subnets**: the same two public subnets →
  **Security group**: `radar-scraper-sg` → **Auto-assign public IP**: **Turned on**.
- **Run task**.

**CLI equivalent** (same network config as the Phase 4 schedule target):

```bash
aws ecs run-task \
  --cluster radar-scraper \
  --task-definition radar-scraper \
  --launch-type FARGATE \
  --count 1 \
  --network-configuration '{
    "awsvpcConfiguration": {
      "subnets": ["<subnet-id-1>", "<subnet-id-2>"],
      "securityGroups": ["<sg-id>"],
      "assignPublicIp": "ENABLED"
    }
  }' \
  --region <REGION>
```

Note the returned `taskArn` (or the task ID from the console's Tasks tab) — the
checklist below refers to it as `<task-id>`.

### 2. Checklist

**☐ Container starts and reaches Running.**
Tasks tab → click the task → **Status** should move Provisioning → Pending →
Running within roughly a minute. If it goes straight to **Stopped** instead, open
the task and check **Stopped reason** first — a bad image URI, a role ARN that
doesn't exist yet, or a missing SSM parameter permission all show up here, before
you go looking anywhere else.

```bash
aws ecs describe-tasks --cluster radar-scraper --tasks <task-id> --region <REGION> \
  --query 'tasks[0].{Status:lastStatus,StoppedReason:stoppedReason}'
```

**☐ Logs appear in CloudWatch.**
The task's own **Logs** tab in the ECS console streams them live — no need to jump
to CloudWatch separately during the run. You should see the structured JSON log
lines from `setup_logging()` (`{"ts": ..., "level": "INFO", ...}`), progress through
collect → fetch → dedup → summarize, and end with a `=== Pipeline Summary ===` block.
Some per-source `ERROR`/`WARNING` lines are expected and not a failure — a handful of
Google News 503s or a blocked LinkedIn company on any given run is normal (see
`src/radar_pipeline/README.md`'s anti-block notes); look for the run finishing with
a summary, not for zero errors.

```bash
aws logs tail /ecs/radar-scraper --since 30m --region <REGION>
# add --follow to watch it live instead of a one-shot fetch
```

**☐ The task finishes with exit code 0.**
Once `lastStatus` is `STOPPED`, check the container's exit code — a full run takes
~12–15 minutes, so don't check this too early.

```bash
aws ecs describe-tasks --cluster radar-scraper --tasks <task-id> --region <REGION> \
  --query 'tasks[0].containers[0].{ExitCode:exitCode,Reason:reason}'
```

**☐ New items land in DynamoDB with the expected keys.**
DynamoDB console → Tables → `radar-articles` → **Explore table items**. You should
see, per new article: one item with `sk = METADATA` (the base article — `pk` looks
like `ARTICLE#<32-char-hex-hash>`), one item with `sk` starting `RAWLINK#` next to
it, and one or more items with `sk` starting `COMPANY#<tag>`. See the [CLI
spot-checks](#3-spot-check-dynamodb-contents-from-the-cli) below for the same check
from a terminal.

### 3. Spot-check DynamoDB contents from the CLI

Run these yourself from CloudShell or your machine — swap in a real company tag and
date range for your data.

**Query `CompanyTimeIndex` (GSI1) for one company across a time range** — this is
the exact access pattern the Phase 6 read API will use for the per-company feed:

```bash
aws dynamodb query \
  --table-name radar-articles \
  --index-name CompanyTimeIndex \
  --key-condition-expression "gsi1pk = :pk AND gsi1sk BETWEEN :from AND :to" \
  --expression-attribute-values '{
    ":pk": {"S": "COMPANY#Bosch"},
    ":from": {"S": "2026-08-01"},
    ":to": {"S": "2026-08-31~"}
  }' \
  --region <REGION>
```

(The trailing `~` on `:to` is a cheap trick, not a typo — each sort key is
`<timestamp>#<hash>`, and `~` sorts after every character the hash suffix can
contain, so `2026-08-31~` includes everything published *on* 2026-08-31, not just
strictly before it.)

**Latest N articles overall** (`LatestIndex`, GSI2 — the homepage feed's query):

```bash
aws dynamodb query \
  --table-name radar-articles \
  --index-name LatestIndex \
  --key-condition-expression "gsi2pk = :pk" \
  --expression-attribute-values '{":pk": {"S": "ARTICLE"}}' \
  --scan-index-forward false \
  --limit 10 \
  --region <REGION>
```

**Fetch one article directly**, if you have its hash (e.g. from the query results
above, or from a CloudWatch log line):

```bash
aws dynamodb get-item \
  --table-name radar-articles \
  --key '{"pk": {"S": "ARTICLE#<article_hash>"}, "sk": {"S": "METADATA"}}' \
  --region <REGION>
```

**Rough total article count** (a `Scan`, filtered to base items only so
company-link/raw-link-pointer items aren't double-counted — fine for an occasional
manual check at this data volume, not something to run on a schedule):

```bash
aws dynamodb scan \
  --table-name radar-articles \
  --filter-expression "sk = :sk" \
  --expression-attribute-values '{":sk": {"S": "METADATA"}}' \
  --select COUNT \
  --region <REGION>
```

If the company query in the first command comes back empty but the latest-overall
query doesn't, check that the company tag matches `configs/radar_sources.yaml`'s
`tag:` field exactly (case-sensitive) — that's the most common reason for an
otherwise-successful run to look empty from one angle only.

### Troubleshooting: `ResourceInitializationError` pulling secrets/registry auth

**Symptom**: the task stops immediately with something like `unable to pull secrets
or registry auth: unable to retrieve secrets from ssm: ... context deadline
exceeded`, before any application log lines appear.

**Cause**: the task's network interface can't reach the SSM (and/or ECR) endpoint at
all — this happens during resource initialization, before the container even starts,
so it's a pure networking problem, not a permissions or app-code one. The specific
case this bit during initial rollout: the subnet had **Auto-assign public IPv4
address** enabled and the task *did* get a public IP, but the subnet's route table
had no `0.0.0.0/0 -> igw-...` route — so that public IP had nothing to route through.
Auto-assign-IP and "is actually a public subnet" are two different, both-required
properties; see the corrected guidance in Phase 4's networking section above.

**Fix**: confirm an Internet Gateway is attached to the VPC, then add the missing
route to the subnet's route table:

```bash
aws ec2 describe-internet-gateways --filters "Name=attachment.vpc-id,Values=<vpc-id>" --region <REGION>
# if empty, create and attach one:
aws ec2 create-internet-gateway --region <REGION>
aws ec2 attach-internet-gateway --internet-gateway-id <igw-id> --vpc-id <vpc-id> --region <REGION>

aws ec2 create-route --route-table-id <rtb-id> --destination-cidr-block 0.0.0.0/0 --gateway-id <igw-id> --region <REGION>
```

Re-run the task (step 1 above) once `describe-route-tables` shows the route.

If that's not it: also check the security group actually allows the outbound traffic
(covered in Phase 4), and that DNS resolution is enabled for the VPC
(`enableDnsSupport`/`enableDnsHostnames`, both on by default and rarely the culprit).

---

*(Later phases — read API, frontend — will each add their own section here as
they're built.)*
