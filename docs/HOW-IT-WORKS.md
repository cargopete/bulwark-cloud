# Bulwark & Bulwark-Cloud — A Field Manual

*How the whole thing works, from the audit engine up to the cloud service.*

---

## Who this is for

You can read and write code, but you've **never touched Python or AWS**. That's exactly the
reader this booklet is written for. Every Python idiom and every AWS service is explained the
first time it shows up, in a short **"Plain English"** box. You won't need to already know what
a Lambda is, or what `pydantic` does — just bring general programming literacy.

The booklet has two parts, and the order matters:

- **Part I — Bulwark** is the actual audit *engine*: a command-line program that audits a smart
  contract. Understand this first, because the cloud is just a way to run it at scale.
- **Part II — Bulwark-Cloud** is the *service* wrapped around the engine: submit a GitHub repo
  over HTTPS, and the audit runs itself on Amazon's servers and hands you back a report.

> **A one-sentence summary to hold in your head:**
> Bulwark is a program that audits a contract. Bulwark-Cloud is the machinery that runs that
> program for you, on demand, in the cloud, and stores the results.

---
---

# PART I — BULWARK (the engine)

## 1. What Bulwark is

Bulwark is a **smart-contract security auditor** built as a command-line tool. You point it at a
Solidity codebase and it runs a **six-pass pipeline** that combines:

- **deterministic tools** (compilers, static analysers) that always give the same answer, and
- **AI agents** (Claude) that reason about the code like a human auditor would,

then **proves or disproves** each suspected bug by writing and running real exploit tests.

The guiding principle is **"no proof, no finding."** An AI claiming "this looks reentrant" is
cheap and often wrong. Bulwark forces every claim through a gate: write a proof-of-concept
exploit, compile it, run it. If the exploit doesn't actually work, the finding is discarded.

Bulwark itself is written in **Rust** (a compiled systems language), but you don't need to read
Rust to understand it — what matters is *what it orchestrates*, not the language it's written in.

## 2. The mental model: a CLI inside a container

Two concepts to get straight first.

> **Plain English — "CLI"**
> A *command-line interface* is a program you run by typing its name in a terminal, like
> `git commit` or `ls`. Bulwark's is `bulwark`. You run `bulwark run` and it does the audit.

> **Plain English — "Docker container"**
> A smart-contract audit needs a precise zoo of tools installed: the Solidity compiler, the
> Foundry test framework, the Slither analyser, the Halmos prover, Claude, and 70-odd AI
> "skills." Installing all that by hand on every machine is misery. A **Docker container** is a
> sealed, pre-built box that already contains every tool at the exact right version. You ship the
> box, not the instructions. Bulwark is distributed as such a box (a Docker *image*), so it runs
> identically on your laptop or on a server.

So: Bulwark is a CLI, and it lives inside a Docker container that has all its dependencies
pre-installed. When you run the container, it runs `bulwark run`, which drives the pipeline.

### The three directories Bulwark cares about

Bulwark juggles three locations, controlled by environment variables. Keep these straight and
everything else falls into place:

| Variable | What it points to | Contains |
|----------|-------------------|----------|
| `AUDIT_DIR` | the **cloned target repo** | the Solidity code being audited |
| `BULWARK_ROOT` | the **project config dir** | `bulwark.toml` + a `context/` folder |
| `BULWARK_INSTALL` | the **tool install dir** | baked-in prompts, schemas, generic knowledge |

The audit's working files land in a sub-folder of the target repo called `audit-workspace/`.

## 3. The inputs: a target and its context

Bulwark needs two kinds of input.

**1. The target** — *what* to audit. Declared in `bulwark.toml`:

```toml
[target]
repo   = "https://github.com/owner/repo.git"
branch = "main"
scope  = ["contracts/", "src/"]   # which directories are in-scope
core_contracts = ["Vault", "Token"]   # the contracts to focus on
```

**2. The context** — *what the auditor needs to know*. These are four Markdown files in
`BULWARK_ROOT/context/`, and they are the single most important thing to understand about
Bulwark's quality:

| File | What it tells the AI |
|------|----------------------|
| `AUDIT_CONTEXT.md` | What the protocol *does* — architecture, trust model, who's privileged, where the money is |
| `PROPERTIES.md` | The **invariants** to verify, written as `P-1`, `P-2`, … ("total supply is conserved", "only the owner can pause") |
| `KNOWN_ISSUES.md` | Accepted risks the AI must **not** report (so it doesn't waste effort re-flagging them) |
| `ATTACK_PATTERNS.md` | A library of known exploit patterns from past audits — generic, shared across all projects |

> **Why this matters enormously.** Bulwark was designed assuming a *human* writes these context
> files before each audit. Feed it the wrong `PROPERTIES.md` and the AI agents go hunting for
> invariants that don't exist in the code. (This is precisely the problem Bulwark-Cloud has to
> solve automatically — see Part II, §7.)

**Resolution order** for context files (first match wins): already present in the target dir →
`BULWARK_ROOT/context/` → `BULWARK_INSTALL/context/`. The exception is `ATTACK_PATTERNS.md`,
which *always* comes from the install dir because it's generic, accumulated knowledge.

## 4. The six passes

`bulwark run` executes six passes in order. Each pass reads the previous passes' outputs and
writes its own results as JSON files into `audit-workspace/`. If a pass fails, the pipeline
stops. Progress is printed to the screen in a machine-readable form — `[pass:2] [status:done]
[duration_s:288]` — which becomes important in Part II.

### Pass 1 — Reconnaissance (no AI)

Pure, deterministic groundwork. No AI involved, so it's fast and free.

- Compiles the code (`forge build`).
- Runs **Slither**, a static analyser that flags suspicious patterns (`slither --json`).
- Extracts structural facts: every externally-callable function (the "entry points"), the storage
  layout of each contract, the inheritance graph, access-control modifiers, arithmetic-heavy code,
  and proxy patterns.

> **Plain English — "static analysis"**
> Reading code *without running it* to spot problems — like a spell-checker for code. Slither
> knows hundreds of Solidity anti-patterns (reentrancy shapes, unchecked math, etc.).

Outputs land in `audit-workspace/recon/` (`entry-points.json`, `storage-layouts.json`,
`slither-results.json`, and more). Pass 1 succeeds if **at least one** package compiles.

### Pass 2 — Multi-agent adversarial analysis (AI)

The heart of the AI work. **Three independent Claude sessions** run *in parallel*, each given a
different personality and goal. They cannot see each other's output:

| Agent | Persona | Hunts for |
|-------|---------|-----------|
| **RED** | Attacker | Exploits that steal funds |
| **BLUE** | Verifier | Tries to prove/disprove each property in `PROPERTIES.md` |
| **GOLD** | DeFi economist | Rounding errors, MEV, flash-loan games |

Each agent reads the recon data and the context files, then writes its raw findings to
`findings/{red,blue,gold}-agent-raw.json`. Afterwards Bulwark **merges and de-duplicates** them
(the same bug found by two agents becomes one finding, flagged as higher-confidence).

> **Why three?** Diversity catches what redundancy can't. An attacker mindset and an economist
> mindset surface different bug classes. Running them blind to each other prevents groupthink.

### Pass 3 — PoC gate (AI + Foundry)

This is the "no proof, no finding" gate. For **every** finding from Pass 2:

1. (Optional) a quick false-positive check throws out obvious noise.
2. Claude writes a **Foundry test** — a real Solidity test that *demonstrates the exploit*. The
   convention: the test **passes** (`[PASS]`) when the attack **succeeds**.
3. Bulwark compiles it (`forge build`). If it won't compile, Claude gets the error and retries
   (up to a couple of times).
4. Bulwark runs it (`forge test`).

The result classifies each finding:

- **Validated** — compiles and the exploit works. A real, proven bug.
- **Inconclusive** — compiles but the test didn't clearly demonstrate the exploit.
- **Unverifiable** — couldn't compile, *but* the underlying package failed to build in Pass 1
  too, so the finding gets the benefit of the doubt.
- **Discarded** — won't compile while everything else does → treated as a false positive, dropped.

> **Plain English — "Foundry / forge"**
> Foundry is the standard testing toolkit for Solidity. `forge` is its command — `forge build`
> compiles, `forge test` runs tests. Bulwark leans on it heavily because a passing Foundry test
> is *objective evidence* an exploit is real.

### Pass 4 — Fuzzing campaign (AI + Foundry)

Claude reads `PROPERTIES.md` and writes **invariant tests** — tests that assert "this should
*always* be true, no matter what." Foundry then hammers them with thousands of random inputs
(default 10,000 runs) trying to find a sequence that breaks the invariant.

> **Plain English — "fuzzing"**
> Throwing huge volumes of random/automated inputs at code to find the one combination that
> breaks it. An "invariant" is a rule that must hold under all of them.

Broken invariants become findings in `fuzzing/fuzzing-findings.json`. (Bulwark can also drive
Medusa and Echidna, two heavier fuzzers, if they're installed.)

### Pass 5 — Formal verification (AI + Halmos)

The most rigorous pass. Claude writes **symbolic tests** for the critical properties, and
**Halmos** runs *bounded model checking* on them.

> **Plain English — "formal verification / symbolic execution"**
> Instead of testing specific values, Halmos reasons about *all possible* values mathematically.
> If it can't find any input that breaks a property (within a bounded search), the property is
> **VERIFIED**. If it finds a concrete counterexample, the property is **VIOLATED** — a bug.

For each target property `P-N`, Halmos looks for a test function named `check_P{N}_…` and runs it,
producing one of: **VERIFIED**, **VIOLATED**, **TIMEOUT** (solver ran out of time — not a bug),
**VACUOUS** (no matching test was generated), or **ERROR**.

> **Important quirk** (and a bug Bulwark-Cloud had to fix): if `bulwark.toml` doesn't say *which*
> properties to verify, Bulwark falls back to a **hardcoded default list** of property IDs. Those
> defaults were written for one specific protocol (The Graph), so on any other codebase they
> match nothing. Part II, §7 explains how the cloud layer fixes this.

### Pass 6 — Adversarial review + report (AI)

A *fresh* Claude session, with no prior context, reads all the accumulated findings and
challenges them — upgrading or downgrading severities, spotting blind spots. Then Bulwark
assembles the final report in two formats:

- **`final-report.json`** — machine-readable: a `findings` array, a `severity_breakdown`, the
  formal `verification` results, and metadata.
- **`final-report.md`** — the human-readable write-up.

A single finding in the JSON looks like:

```json
{
  "id": "F-001",
  "severity": "Critical",
  "title": "Incorrect weighted-average calculation in profit locking",
  "contract": "TokenizedStrategy.sol",
  "function": "report()",
  "poc_status": "compiles_but_inconclusive",
  "property_violated": "P-7"
}
```

## 5. How Bulwark talks to Claude

Every AI pass shells out to **Claude Code in "headless" mode** — i.e. non-interactively, no chat
window. The invocation looks like:

```bash
claude -p "<the prompt>" --max-turns 80 --verbose --model haiku
```

- `-p` passes the prompt; `--max-turns` caps how long it can work; `--model` picks haiku (cheap),
  sonnet (better), or opus (best).
- Authentication is via an `ANTHROPIC_API_KEY` environment variable.
- What Claude is *allowed to do* (read files, write files, run shell commands) is governed by a
  `settings.json` permission file baked into the container. For example, the fuzzing pass denies
  Claude shell access so it can't get stuck in a compile-fix loop — it writes files only, and
  Bulwark does the compiling.

## 6. The external tools

Bulwark is an **orchestrator** — most of its power comes from coordinating best-in-class tools:

| Tool | Role | Invoked as |
|------|------|-----------|
| **Foundry (`forge`)** | compile & test Solidity | `forge build`, `forge test`, `forge inspect` |
| **Slither** | static analysis | `slither <dir> --json` |
| **Halmos** | formal verification | `halmos --function check_P7_ --loop 5 …` |
| **Claude Code** | the AI reasoning | `claude -p …` |
| **Medusa / Echidna** | heavy fuzzing (optional) | — |

It finds each tool via your `PATH` (or explicit paths in `bulwark.toml`), and degrades gracefully
if an optional one is missing.

## 7. Configuration: `bulwark.toml`

> **Plain English — "TOML"**
> A simple config file format (like INI or YAML). `[section]` headers group related settings.

Everything is tunable per-pass. A representative slice:

```toml
model = "haiku"                 # global default model

[passes.agents]
agents          = ["red", "blue", "gold"]
timeout_minutes = 60

[passes.poc]
max_retries = 2                 # retries per failing PoC
fp_check    = true              # run a false-positive pre-check

[passes.fuzzing]
fuzz_runs = 10000
model     = "sonnet"            # override: better at writing compilable tests

[passes.formal]
solver_timeout    = 300
loop_bound        = 5
target_properties = ["P-1", "P-7"]   # which properties Halmos checks
```

Each pass also has an `enabled` flag (all default to `true`), so you can run a subset.

## 8. The workspace and the report

Everything a run produces lives under `audit-workspace/`:

```
audit-workspace/
├── pipeline-status.json     # checkpoint: each pass's status + duration
├── recon/                   # Pass 1 — entry points, storage, slither, …
├── findings/                # Pass 2 — per-agent + merged findings
├── pocs/                    # Pass 3 — validated / discarded / .t.sol files
├── fuzzing/                 # Pass 4 — invariant tests + results
├── formal/                  # Pass 5 — symbolic tests + halmos logs
├── review/                  # Pass 6 — adversarial review
├── final-report.json        # the report (machine-readable)
└── final-report.md          # the report (human-readable)
```

The `pipeline-status.json` checkpoint also lets a run **resume** from a given pass instead of
restarting.

## 9. The Docker image

The container is built in two stages: compile the Rust binary, then assemble a runtime image with
every tool installed — Foundry, Slither, Halmos, the Solidity compiler manager, Node.js, and
Claude Code. It runs as a non-root `auditor` user, and `BULWARK_INSTALL` points at that user's
home directory where the baked-in prompts, schemas, and `ATTACK_PATTERNS.md` live.

## 10. Part I recap

Bulwark = a Rust CLI in a Docker box that audits a Solidity repo via six passes (recon → AI agents
→ PoC gate → fuzzing → formal → review), proving each finding before reporting it, and emits a
JSON + Markdown report. Its quality hinges on good **context files**. Its weakness, for automation,
is that it expects a *human* to write those context files and to tell it which properties to verify.

Hold that thought — it's the exact gap Bulwark-Cloud fills.

---
---

# PART II — BULWARK-CLOUD (the service)

## 0. Why a cloud layer at all

Running Bulwark by hand means: provision a beefy machine, install Docker, write the context files,
run the container for ~20–45 minutes, collect the files. Bulwark-Cloud turns all of that into a
single HTTP request:

```
POST /v1/audits  { "repo": "...", "branch": "...", "scope": [...], "model": "haiku" }
   → { "job_id": "01J...", "status": "PENDING" }

GET  /v1/audits/{job_id}            → live status + per-pass progress
GET  /v1/audits/{job_id}/findings   → the findings
GET  /v1/audits/{job_id}/report     → a download link for the full report
```

The audit runs asynchronously on Amazon's servers; you poll for status and fetch the report when
it's done. Everything is **serverless** — there's no server you keep running and pay for around the
clock; resources spin up per-audit and bill by the second.

## 1. Crash course: the AWS pieces

Bulwark-Cloud is assembled from a dozen Amazon Web Services building blocks. Here's each one in
plain English, grouped by what it's *for*.

**Running code**
- **AWS Lambda** — runs a *function* on demand, with no server to manage. You hand Amazon your
  code; it runs when called and you pay per invocation. Used here for the fast, short tasks (the
  API, and the small "glue" steps).
- **ECS Fargate** — runs a *Docker container* on demand, again with no server to manage. Used for
  the long, heavy job: actually running Bulwark. (Lambda has a 15-minute ceiling and limited
  resources; an audit needs ~45 minutes and 16 GB of RAM — hence Fargate.)

**Front door & coordination**
- **API Gateway** — the public HTTPS front door. It receives web requests, checks the API key,
  and forwards them to the API Lambda. It exposes the API under a *stage* named `v1`.
- **Step Functions** — a *workflow engine*. It runs a sequence of steps ("submit → run the audit →
  index the findings → notify"), handles retries, and branches to a failure path if anything dies.
  Think of it as a flowchart that AWS executes reliably.

**Storage**
- **S3** (Simple Storage Service) — file storage (object storage). The audit's artefacts and the
  final report live here.
- **DynamoDB** — a fast NoSQL database (key-value/document). It holds the *state* of every job:
  status, per-pass progress, and findings.
- **Secrets Manager** — encrypted storage for secrets, like the Anthropic API key. Code fetches the
  secret at runtime instead of hardcoding it.

**Edges & glue**
- **CloudFront** — Amazon's CDN (content delivery network). Serves the web dashboard fast and over
  HTTPS.
- **CloudWatch** — logging, metrics, dashboards, and alarms. Everything's logs and graphs land here.
- **SNS** (Simple Notification Service) — pub/sub messaging. Used to announce "audit complete" /
  "audit failed" events.

**Plumbing**
- **VPC** (Virtual Private Cloud) — a private network for your resources. The Fargate task runs
  inside it with no public IP, reaching the internet only for outbound HTTPS.
- **IAM** (Identity and Access Management) — permissions. Every component is granted the *minimum*
  set of actions it needs ("this Lambda may read this table and nothing else").

**And the one that ties it together**
- **CDK** (Cloud Development Kit) — instead of clicking around the AWS console, you *describe* all
  the above resources in code (here, Python). CDK turns that code into CloudFormation, Amazon's
  provisioning system, and creates everything reproducibly. This is **"infrastructure as code."**

## 2. Crash course: the Python pieces

The application code is Python. The libraries you'll see:

- **FastAPI** — a web framework for building HTTP APIs. You write a function and decorate it with
  the route (`@router.post("/audits")`) and FastAPI handles the HTTP plumbing.
- **Mangum** — a tiny adapter that lets a FastAPI app run *inside a Lambda*. API Gateway speaks
  Lambda's event format; Mangum translates that to/from what FastAPI expects. One line:
  `handler = Mangum(app)`.
- **Pydantic** — data validation via Python type hints. You declare a class with typed fields and
  Pydantic guarantees incoming data matches (or rejects it). It's how the API knows a submission
  has a valid `repo`, `branch`, `scope`, `model`.
- **pydantic-settings** — the same idea for *environment variables*: declare the env vars you
  expect as a typed class, and it reads and validates them.
- **boto3** — the AWS SDK for Python. Every call to DynamoDB, S3, Secrets Manager, etc. goes
  through boto3.
- **structlog** — structured (JSON) logging, so CloudWatch logs are machine-parseable.
- **python-ulid** — generates **ULIDs**: like UUIDs but time-sortable and URL-safe. Used for
  `job_id`s.

> **Plain English — "ASGI", "serverless"**
> *ASGI* is the standard interface modern Python web apps speak; FastAPI is an ASGI app, and Mangum
> bridges it to Lambda. *Serverless* means you never manage a running server — code runs on demand
> and you pay per use.

## 3. The big picture

Here is the whole system on one page. Follow the arrows:

```
                    ┌─────────────────────────┐
   Browser ───────▶ │ CloudFront + S3 (the SPA dashboard) │
                    └─────────────────────────┘
                               │  (you type API URL + key into Settings)
                               ▼
  HTTPS request ──▶ API Gateway (stage "v1", checks x-api-key)
                               │
                               ▼
                     API Lambda (FastAPI + Mangum)
                       │            │
       writes job ─────┘            └───── starts ──▶ Step Functions state machine
       to DynamoDB                                         │
                                                           ├─ 1. SubmitJob Lambda  (PENDING→PROVISIONING)
                                                           ├─ 2. RunAudit  ──▶ ECS Fargate task
                                                           │        (the orchestrator runs Bulwark)
                                                           │        ├─ writes artefacts + report ─▶ S3
                                                           │        └─ writes live progress ─────▶ DynamoDB
                                                           ├─ 3. IndexFindings Lambda (report→DynamoDB, →COMPLETED)
                                                           └─ 4. NotifyComplete ─▶ SNS
                                                              (on any error: MarkFailed → NotifyFailed)
```

Two distinct timelines run here:

1. **The request timeline** (milliseconds): browser → API Gateway → API Lambda → write to
   DynamoDB + kick off Step Functions → respond `{job_id, PENDING}`. The caller gets an answer
   immediately; the audit hasn't run yet.
2. **The job timeline** (~20–45 minutes): Step Functions drives the audit to completion in the
   background. The caller *polls* `GET /audits/{job_id}` to watch it progress.

## 4. The API (a FastAPI app running in a Lambda)

The public API is a FastAPI application, wrapped by Mangum so it runs as a single Lambda behind API
Gateway. API Gateway requires a valid `x-api-key` header on every route **except** `/health`, and
enforces a usage plan (rate limits + a 1,000-requests/day quota) before the Lambda is ever invoked.

> Note on paths: API Gateway serves everything under the `v1` stage, so the public URL is
> `https://….amazonaws.com/v1/audits`. Internally, Mangum strips the stage, so the FastAPI code
> registers the route as just `/audits`. (Getting this wrong caused a 404 bug early on.)

The endpoints:

| Method & path | Auth | What it does |
|---------------|------|--------------|
| `POST /audits` | ✓ | Create an audit. Generates a ULID `job_id`, writes a `PENDING` job to DynamoDB, starts the Step Functions execution, returns `{job_id, status, created_at, status_url}`. |
| `GET /audits` | ✓ | List audits (optionally filtered by status), paginated. |
| `GET /audits/{job_id}` | ✓ | Full status: job metadata + per-pass progress + findings counts. |
| `POST /audits/{job_id}/cancel` | ✓ | Stop a running audit (stops the Step Functions execution; status → `CANCELLING`). |
| `DELETE /audits/{job_id}` | ✓ | Soft-delete (sets a `deleted_at` marker; the row stays). |
| `GET /audits/{job_id}/findings` | ✓ | List findings (filter by severity / validated-only). |
| `GET /audits/{job_id}/findings/{finding_id}` | ✓ | One finding in full detail. |
| `GET /audits/{job_id}/report?format=md\|json` | ✓ | Returns a short-lived **pre-signed S3 URL** to download the report. |
| `GET /health` | — | Liveness check, no key required. |

> **Plain English — "pre-signed URL"**
> The report sits in a private S3 bucket. Rather than proxy the file through the API, S3 can mint a
> temporary URL that grants read access to *that one object* for a few minutes (here, 300 seconds).
> Anyone with the link can download the report until it expires — no API key needed to follow it.

The API's typed inputs/outputs are Pydantic models defined once in a **shared package** and reused
across the API, the orchestrator, and the Lambdas — so the shape of an `Audit` or a `Finding` is
defined in exactly one place.

## 5. The database: DynamoDB single-table design

All job state lives in **one** DynamoDB table, `bulwark-cloud-state`. This is the idiomatic
DynamoDB pattern: rather than many tables, you use one, and encode *different kinds of row* via the
key structure.

> **Plain English — "partition key / sort key"**
> Every DynamoDB row is addressed by a **partition key (PK)** plus a **sort key (SK)**. Rows
> sharing a PK are stored together and can be range-queried by SK. So if every row about job
> `01J…` shares `PK = JOB#01J…`, you can fetch *everything* about that job in one query, and use
> the SK to pick which slice (the metadata? the passes? the findings?).

The three row types, all under the same `PK = JOB#{job_id}`:

| Row | SK | Holds |
|-----|----|-------|
| **Job metadata** | `METADATA` | status, repo, branch, scope, model, timestamps |
| **Pass progress** | `PASS#01` … `PASS#06` | each pass's status, duration, findings emitted, token usage |
| **Finding** | `FINDING#F-001` … | one row per finding (severity, title, contract, function, PoC status) |

On top of the table sit two **Global Secondary Indexes** — alternative ways to query the same data:

- **GSI1** keyed by `SEVERITY#{level}` — "show me all CRITICAL findings across every job."
- **GSI2** keyed by `STATUS#{status}` — "show me all RUNNING jobs, newest first."

> **Plain English — "GSI"**
> A *Global Secondary Index* is a second, automatically-maintained view of the table organised by
> a different key, so you can query along a dimension the main key doesn't support.

The table is **pay-per-request** (you're billed per read/write, not for idle capacity) with
point-in-time recovery enabled.

## 6. The workflow: the Step Functions state machine

When the API starts an audit, it kicks off a **state machine** named `bulwark-cloud-audit-pipeline`.
This is the reliable, retrying flowchart that carries a job from submission to completion. Its
happy path is four steps:

```
SubmitJob  →  RunAudit  →  IndexFindings  →  NotifyComplete
(Lambda)      (Fargate)     (Lambda)          (SNS)
```

1. **SubmitJob** (Lambda) — flips the job from `PENDING` to `PROVISIONING`. Quick bookkeeping.
2. **RunAudit** (Fargate) — the big one. Launches the Bulwark container as an ECS Fargate task and
   **waits** for it to finish (the `.sync` / "run job" integration). The state machine passes the
   job parameters into the container as environment variables (see §7).
3. **IndexFindings** (Lambda) — reads the finished `final-report.json` from S3, writes one
   DynamoDB row per finding, and marks the job `COMPLETED`.
4. **NotifyComplete** (SNS) — publishes an "audit complete" event.

Every step has a **retry policy** (e.g. RunAudit retries twice with backoff on failure). And the
whole thing has a **catch-all**: if *any* step errors, the machine jumps to a failure branch —
**MarkFailed** (Lambda; writes `FAILED` + the error detail to DynamoDB) → **NotifyFailed** (SNS).

> **Why a state machine instead of just code?** Because the work spans ~45 minutes and multiple
> services, and things fail. Step Functions gives durable execution, automatic retries, a visual
> trace of where a job is, and a guaranteed failure path — without you writing any of that
> orchestration logic yourself.

The three small Lambdas it calls (`submit`, `index_findings`, `mark_failed`) are deliberately tiny
and self-contained — each is a single `handler.py` file bundled with just the boto3 SDK.

## 7. The orchestrator: what actually runs inside the Fargate task

This is the bridge between "a generic audit CLI" and "a cloud service," and it's where the most
important cloud-specific logic lives. When Step Functions launches the Fargate task, it runs a
Python program — the **orchestrator** — which drives one audit from start to finish.

The container receives its instructions as environment variables set by the state machine:

| Env var | From | Example |
|---------|------|---------|
| `JOB_ID` | the job | `01J…` |
| `TARGET_REPO` | submission | `https://github.com/yearn/tokenized-strategy` |
| `TARGET_BRANCH` | submission | `master` |
| `TARGET_SCOPE` | submission (JSON) | `["src/"]` |
| `BULWARK_MODEL` | submission | `haiku` |
| `DYNAMO_TABLE`, `S3_BUCKET`, `SECRET_ARN_ANTHROPIC`, `AWS_REGION` | the task definition | — |

The orchestrator's lifecycle (`run()`), step by step:

1. **Mark RUNNING** — update the job row in DynamoDB.
2. **Fetch the Anthropic key** — read it from Secrets Manager (never hardcoded).
3. **Clone the repo** — `git clone --depth=1` the target into `/tmp/audit/{job_id}/target`.
4. **Generate context** — *the key cloud-specific step* (see below).
5. **Render `bulwark.toml`** — write the config, including the property IDs for the formal pass.
6. **Run Bulwark** — launch `bulwark run` as a subprocess and stream its output.
7. **Upload artefacts** — push the whole workspace and the report to S3.
8. **Mark COMPLETED / FAILED** — based on Bulwark's exit code.

### The context-generation step (the crucial fix)

Recall from Part I that Bulwark expects a *human* to hand-write the `context/` files, and that
without them it falls back to context for an unrelated protocol — producing a useless audit. A
cloud service taking arbitrary repos has no human in the loop.

The solution: before running Bulwark, the orchestrator launches its **own headless Claude
session** that reads the in-scope contracts and *writes the context files itself*:

```
claude -p "read the contracts in ./target, write AUDIT_CONTEXT.md,
           PROPERTIES.md and KNOWN_ISSUES.md into ./context"
```

It produces target-specific `AUDIT_CONTEXT.md`, `PROPERTIES.md` (invariants `P-1`, `P-2`, …
derived from the actual code), and `KNOWN_ISSUES.md`. If the session fails, neutral fallback files
are written so the audit never silently inherits the wrong protocol's context.

Then — because Bulwark's formal pass otherwise defaults to the *wrong* (hardcoded) property IDs —
the orchestrator parses the freshly-generated `PROPERTIES.md`, extracts every `P-N` heading, and
writes them into `[passes.formal] target_properties` in `bulwark.toml`. Now Halmos verifies the
properties that actually exist in *this* codebase.

### Streaming live progress

While `bulwark run` executes, it prints lines like `[pass:2] [status:done] [duration_s:288]
[findings:14]`. The orchestrator parses each line and **updates the corresponding `PASS#` row in
DynamoDB in real time**. That's how the dashboard can show a live pass-by-pass timeline without
the audit having finished.

### Exit codes

Bulwark's exit code tells the orchestrator (and Step Functions) what to do:

| Code | Meaning | Result |
|------|---------|--------|
| `0` | success | mark COMPLETED |
| `1–9` | job-level failure (won't compile, unreachable repo) | mark FAILED, **no retry** |
| `10–19` | transient infra hiccup (S3/DynamoDB/Anthropic flaky) | Step Functions **retries** |
| `20–29` | terminal infra failure (bad config) | fail, alert ops, no retry |

This separation matters: a repo that doesn't compile shouldn't be retried (it'll fail again), but a
momentary network blip should be.

## 8. Storage and the report's journey

- During the run, the orchestrator uploads the entire `audit-workspace/` to
  `s3://…/{job_id}/workspace/…`, and the final report to `s3://…/{job_id}/report/final-report.{md,json}`.
- `IndexFindings` reads `final-report.json` back from S3 and writes the findings into DynamoDB.
- When you call `GET /audits/{job_id}/report`, the API mints a 5-minute pre-signed URL to the S3
  object and returns it; your browser downloads directly from S3.

The bucket has lifecycle rules to control cost: intermediate `workspace/` artefacts expire after 90
days, and objects shift to cheaper "intelligent tiering" storage after 30.

> **A subtle correctness detail.** `IndexFindings` writes the findings and *then* marks the job
> `COMPLETED` — but DynamoDB reads are "eventually consistent" by default, so a client that sees
> `COMPLETED` and immediately lists findings could momentarily get zero. The findings queries
> therefore use **strongly-consistent reads** (`ConsistentRead=True`) so a reader who sees
> COMPLETED always sees the findings.

## 9. The dashboard

The frontend is a single self-contained HTML/CSS/JavaScript file — no build step, no framework.
It's hosted in a private S3 bucket and served via CloudFront over HTTPS.

You paste your API URL and API key into a Settings panel (stored in the browser's `localStorage`),
and from then on it: submits audits, lists them, shows a live per-pass timeline (polling
`GET /audits/{job_id}` every ~12 seconds while a job is active), lists findings, and opens the
report via the pre-signed URL. It's a thin client — all the logic lives in the API.

## 10. Observability, networking, security

**Observability (CloudWatch).** Two dashboards — an operational one (audits completed/failed,
audit durations, per-pass durations) and a cost one (Anthropic token spend, input vs output). Two
alarms fire to SNS: audit duration p99 over 90 minutes, and Anthropic daily spend over \$50.

**Networking (VPC).** The Fargate task runs in **private subnets** with **no public IP** — it can
make outbound HTTPS (to clone repos and call Anthropic) but nothing can reach it. To avoid paying
the NAT gateway for AWS-internal traffic, the VPC has **endpoints** for S3, DynamoDB, ECR (the
Docker image registry — image pulls are ~1.8 GB each), Secrets Manager, and CloudWatch Logs, so
that traffic stays on Amazon's backbone.

**Security (IAM).** Least privilege throughout. The Fargate task role may write job artefacts to
S3, update the DynamoDB table, and read *only* the Anthropic secret. The API Lambda may read/write
the table, read S3, and start/stop *this* state machine — nothing more. Each Lambda gets its own
scoped role.

## 11. Infrastructure as code: the six CDK stacks

All of the above is *described in Python* using CDK and grouped into six **stacks** (a stack is a
unit of resources deployed together). `infra/app.py` wires them up, passing outputs of one stack
into the next:

| Stack | Creates |
|-------|---------|
| **Network** | the VPC, subnets, NAT, and VPC endpoints |
| **Storage** | the S3 bucket, the DynamoDB table + GSIs, the Secrets Manager secrets, the SNS topic |
| **Compute** | the ECR repo, ECS cluster, the Fargate **task definition**, the **state machine**, and the three workflow Lambdas |
| **Api** | the API Gateway, the API Lambda, the shared-code Lambda layer, the API key + usage plan |
| **Observability** | the CloudWatch dashboards and alarms |
| **Frontend** | the S3 site bucket, CloudFront distribution, and dashboard deployment |

> **Plain English — why it's split into stacks (and a war story).** CloudFormation won't let you
> change a value that one stack *exports* while another stack is *importing* it. The Fargate task
> definition's identifier changes on every deploy, and it used to live in a separate stack that the
> rest imported — so every deploy hit an "export in use" wall. The fix was to **merge** that into
> the Compute stack so the volatile value never crosses a stack boundary. Lesson: keep things that
> change together *in the same stack*.

## 12. CI/CD: how a `git push` becomes a deployment

> **Plain English — "CI/CD"**
> *Continuous Integration / Continuous Deployment*: automation that tests and deploys your code on
> every push, via GitHub Actions (workflows defined in `.github/workflows/`).

On every push to `main`:

1. **CI** runs the test suite, the linter, and the type-checker. If any fail, the deploy is blocked.
2. **Deploy** (gated on CI) then, in order:
   - `cdk bootstrap` (idempotent setup),
   - deploy **Network + Storage + Compute** first — this creates the ECR repository so there's
     somewhere to push the image,
   - build the Bulwark base Docker image, build the orchestrator image on top, and push it to ECR
     tagged with the git commit SHA,
   - deploy the remaining stacks (**Api, Observability, Frontend**).

Authentication to AWS uses **OIDC** (GitHub proves its identity to AWS directly, so there are no
long-lived AWS keys stored in GitHub). A *concurrency group* ensures two pushes can't deploy on top
of each other.

## 13. End-to-end: the life of one audit

Putting it all together, here's what happens when you audit Yearn's `tokenized-strategy`:

1. **You** `POST /v1/audits {repo, branch, scope:["src/"], model:"haiku"}` with your API key.
2. **API Gateway** checks the key and forwards to the **API Lambda**.
3. The Lambda mints a `job_id`, writes a `PENDING` row to DynamoDB, starts the **state machine**,
   and returns `{job_id, PENDING}` — all within a fraction of a second.
4. **SubmitJob** flips the job to `PROVISIONING`.
5. **RunAudit** launches the **Fargate** container. The **orchestrator**: marks `RUNNING`, fetches
   the Anthropic key, clones the repo, **generates Yearn-specific context** (15 properties
   `P-1…P-15`), writes `bulwark.toml` with those property IDs, and runs **Bulwark**.
6. **Bulwark** runs its six passes (~30 min): recon compiles the Foundry project, three AI agents
   hunt for bugs, the PoC gate validates them, fuzzing and Halmos test the 15 invariants, and the
   review assembles a report flagging a Critical in `TokenizedStrategy.report()`. The orchestrator
   streams each pass's progress into DynamoDB live, then uploads the report to S3.
7. **IndexFindings** reads the report from S3, writes the finding rows, marks the job `COMPLETED`.
8. **NotifyComplete** publishes the event.
9. **You** poll `GET /audits/{job_id}` throughout (seeing passes tick over), then fetch
   `GET /audits/{job_id}/report` to download the write-up via a pre-signed S3 link.

## 14. Honest limitations

The plumbing works end-to-end, proven across multiple protocols — but be clear-eyed about what
that does and doesn't mean:

- **The pipeline runs correctly; the audit's *judgement* still needs a human.** AI-flagged
  "Criticals" can be false positives (Halmos may verify a flawed abstract model, not the real
  contract). Treat findings as *leads* — especially any with `poc_validated: false`.
- **Formal coverage is patchy.** Some properties yield no symbolic test or time out. The pass runs;
  its depth on complex contracts is a known limitation.
- **One target, one model at a time.** No concurrency/scale testing, single NAT gateway — fine for
  v0.1, not yet hardened for heavy production traffic.

## 15. Glossary (quick reference)

| Term | One-liner |
|------|-----------|
| **Lambda** | run a function on demand, no server |
| **Fargate** | run a Docker container on demand, no server |
| **API Gateway** | the public HTTPS front door + API-key enforcement |
| **Step Functions** | a reliable, retrying workflow / flowchart engine |
| **S3** | file (object) storage |
| **DynamoDB** | fast NoSQL key-value database |
| **GSI** | an alternative index over a DynamoDB table |
| **Secrets Manager** | encrypted storage for keys/passwords |
| **CloudFront** | CDN that serves the dashboard fast over HTTPS |
| **CloudWatch** | logs, metrics, dashboards, alarms |
| **SNS** | pub/sub event notifications |
| **VPC** | a private network for your resources |
| **IAM** | permissions (who may do what) |
| **CDK** | describe AWS infrastructure in code (Python) |
| **FastAPI** | Python web-API framework |
| **Mangum** | adapter so FastAPI runs inside a Lambda |
| **Pydantic** | validate data via Python type hints |
| **boto3** | the Python AWS SDK |
| **pre-signed URL** | a temporary link granting access to one S3 object |
| **ULID** | a sortable, URL-safe unique ID |
| **OIDC** | lets GitHub authenticate to AWS without stored keys |

---

*Built on [Bulwark](https://github.com/cargopete/bulwark). Generated as living documentation for
bulwark-cloud v0.1.0.*
