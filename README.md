# CDC Prompt Sync Pipeline Training

> **Note:** This is a from-scratch recreation of an architectural pattern I built at my employer, not the original production code -- rebuilt with a fabricated/generic domain and no proprietary business logic, credentials, or internal resource identifiers.

A training-only reconstruction of a change-data-capture (CDC) pipeline that
keeps an LLM prompt-management SaaS (Langfuse) in sync with a relational
database, without the application ever writing to Langfuse directly. It is
modeled on a real production pipeline, but the proprietary domain has been
replaced with a fabricated, generic "customer support response guideline"
domain, and every resource ID / hostname / credential has been made up (see
[What's fabricated vs. real](#whats-fabricated-vs-real)).

## The core idea

A support team maintains hierarchical **guidelines** for how automated
responses should be worded, in a MariaDB database:

```
queue
 └─ scenario
     └─ guideline_group (belongs to a "tier": e.g. tone, policy)
         └─ guideline (a "text" snippet, or a "toggle" -- a named
                        on/off flag referenced by, not inlined into,
                        the combined prompt)
```

Whenever a row changes, every valid combination of guideline_groups across
tiers for that scenario needs its Langfuse prompt regenerated (a cartesian
product -- see [Combination generation](#combination-generation)). Rather
than have the application call Langfuse synchronously on every write (slow,
and couples an unrelated write path to a third-party API's availability),
this pipeline reacts to the *database change itself* and does the sync
asynchronously, out of band.

## Two paths into the same processor, one architectural teaching point

```
                    ┌─────────────────────────────┐
   Production:      │  MariaDB (binlog)            │
                    └──────────────┬───────────────┘
                                   │ AWS DMS (CDC, before/after images)
                                   v
                    ┌─────────────────────────────┐
                    │  Kinesis stream               │
                    └──────────────┬───────────────┘
                                   v
                    ┌─────────────────────────────┐
                    │  kinesis_handler (Node.js)     │  decodes DMS records,
                    │                                 │  synthesizes a DynamoDB-
                    └──────────────┬───────────────┘  Streams-shaped REMOVE
                                   │ direct Lambda invoke  event, and invokes
                                   v                       dynamodb_handler
                    ┌─────────────────────────────┐       directly.
                    │  dynamodb_handler (Python)     │◄────────────────┐
                    └──────────────┬───────────────┘                  │
                                   │                                  │ native DynamoDB
                                   v                                  │ Streams trigger,
                    ┌─────────────────────────────┐                  │ filtered to REMOVE
                    │  SQS (SYNC_GUIDELINES,        │                  │ only
                    │       SYNC_COMBINATIONS,      │      ┌──────────┴──────────┐
                    │       DEPRECATE)              │      │ DynamoDB trigger-bus │
                    └──────────────┬───────────────┘      │ table (TTL + Streams) │
                                   v                       └──────────┬──────────┘
                    ┌─────────────────────────────┐                  │ TTL-expiry
                    │  sqs_processor (Python)        │                  │ delete
                    │  -> Langfuse create/get_prompt │       Local/lower envs:
                    └─────────────────────────────┘       producers write a
                                                           short-TTL item here.
```

**DynamoDB is used purely as a durable *trigger bus*, not a data store.**
Nothing ever reads an item back out of `GuidelineTrackingTable` -- what
matters is only its eventual TTL-expiry *delete*, which DynamoDB Streams
reports as a `REMOVE` event with the deleted item still attached as
`OldImage`. That gives any producer a durable, replayable "a change
happened, here's its data" signal, without needing to know who (if anyone)
is listening. In production, DMS/Kinesis bypasses this table entirely --
the `kinesis_handler` bridge Lambda decodes the real CDC record and
synthesizes an equivalent `REMOVE`-shaped event so the downstream
`dynamodb_handler` never has to know or care which upstream path triggered
it. Locally (see `template.yaml` and `docker-compose.yml`), the table's
native DynamoDB Streams trigger is used directly, filtered to `REMOVE`
events only.

Local DynamoDB emulators (`dynamodb-local`) don't enforce native TTL
expiry, so `dynamodb_ttl_cleanup` is a small scheduled Node.js Lambda that
scans for and deletes expired items itself, to simulate real AWS TTL
behavior in local dev.

## Combination generation

`dynamodb_handler/combinations.py` computes the cartesian product of
guideline_groups across tiers for a scenario (`generate_combinations`).
Guideline **text** rows are inlined into the combined prompt body; guideline
**toggle** rows are instead rendered as a Langfuse prompt *reference*
(e.g. `{{ prompt('toggle_101') }}`) so a toggle's on/off content can change
independently without invalidating every combination that mentions it.

`db_utils.py::fetch_related_data` re-reads only the MariaDB rows a changed
item could possibly affect. The key insight (documented in that module's
docstring): a changed `guideline_group` or `guideline` only ever needs its
**own row**, plus every guideline_group from every **other** tier -- never
its own same-tier siblings, since those siblings form entirely separate,
unaffected combinations.

## Staged SQS delivery

`dynamodb_handler` sends two messages per affected scenario: `SYNC_GUIDELINES`
immediately (`DelaySeconds=0`), and `SYNC_COMBINATIONS` with a computed
delay (`max(5, len(unique_guidelines) // 5)` seconds). Combination prompts
*reference* guideline prompts by name, so they must not sync before the
guidelines they depend on exist in Langfuse.

**Deliberate correction from the original system:** the real pipeline this
trains on uses **FIFO** SQS queues. FIFO queues do not support per-message
`DelaySeconds` at all -- so this training intentionally uses a **standard**
queue instead, to make the delay-based staging pattern actually work as
described. If you need FIFO's strict ordering/exactly-once guarantees in a
real system, you'd need a different staging mechanism (e.g. an explicit
dependency check before syncing combinations, rather than a timed delay).

## Layout

```
dynamodb_handler/       # Python: DynamoDB-Streams-triggered dispatcher
  db_utils.py              cascading MariaDB fetch (same-tier-exclusion insight)
  combinations.py          cartesian product + prompt name/body composition
  dynamodb_processor.py    lambda_handler, promote-to-recompute-scope, staged SQS send
sqs_processor/           # Python: SQS-triggered Langfuse sync
  prompt_naming.py         tiny duplicated helpers (see below)
  prompt_sync.py           run_with_timeout, sanitize_label, sync_guidelines/combinations
  sqs_processor.py         lambda_handler
kinesis_handler/         # Node.js: DMS-via-Kinesis -> synthetic REMOVE event bridge
dynamodb_ttl_cleanup/    # Node.js: scheduled sweeper simulating TTL expiry locally
tests/                   # 33 tests: fakes.py (in-memory MariaDB/SQS/Langfuse fakes)
infra/cdk/               # TypeScript CDK: VPC, DMS (MariaDB source -> Kinesis target),
                         # Kinesis stream, DynamoDB table+Streams, SQS+DLQ, 3 Lambdas,
                         # shared Python dependency layer, per-environment config
template.yaml            # SAM template for local dev (dynamodb-local/kinesalite/
                         # elasticmq/mariadb via docker-compose.yml)
```

`sqs_processor/prompt_naming.py` deliberately **duplicates** two tiny
helpers from `dynamodb_handler/combinations.py` rather than sharing them via
a package or layer. This mirrors the real system's structure: the shared
Lambda layer (`infra/cdk/lib/stack.ts`'s `SharedPythonDependencyLayer`)
carries only third-party dependencies, never domain code, so each Lambda's
package stays fully self-contained and independently deployable.

## What's fabricated vs. real

This training is modeled on a real ETS pipeline that syncs an exam
item-scoring rule hierarchy (program/test/rule_set/rule/toggle) to
Langfuse. Per this training series' "no proprietary business logic"
constraint, **no real domain logic, table/column names, resource IDs,
VPC/subnet/security-group IDs, or internal hostnames are reproduced
anywhere here**. Instead:

- The domain is a fictional "customer support response guideline"
  hierarchy (queue/scenario/guideline_group/guideline), mapped one-to-one
  onto the real hierarchy's shape (program/test/rule_set/rule).
- Langfuse itself is kept as the sync target since it's a legitimate
  third-party product, not proprietary IP.
- All CDK VPC names, subnet IDs, DMS server names, and account/region
  values in `infra/cdk/lib/config/environments.ts` are made up; `cdk synth`
  uses `ec2.Vpc.fromVpcAttributes` with fictional IDs (rather than
  `fromLookup` against a real account) so it synthesizes deterministically
  in CI without AWS credentials.

## Running it

### Tests

```bash
pip install -r requirements-dev.txt
pytest -q            # 33 tests, no real MariaDB/AWS/Langfuse needed
flake8 dynamodb_handler sqs_processor tests
mypy
```

### Local dev stack (SAM + docker-compose)

```bash
docker compose up -d              # dynamodb-local, kinesalite, elasticmq, mariadb
sam build
sam local start-lambda            # or invoke individual functions per template.yaml
```

### CDK (infrastructure)

```bash
cd infra/cdk
npm install
npx cdk synth        # verified to synthesize cleanly in this training's CI
```

## Exercises

1. **Replace the timed-delay staging with an explicit dependency check.**
   Instead of `SYNC_COMBINATIONS` waiting on a computed `DelaySeconds`,
   have `sqs_processor` call Langfuse's `get_prompt` for each referenced
   guideline before syncing a combination, and re-queue (with backoff) if
   any are missing. Compare the failure modes of each approach.
2. **Add a `DEPRECATE` sweep for orphaned combinations.** Currently a
   deleted leaf/mid-tier row only deprecates its own prompt
   (`process_remove_record`'s `DEPRECATE` branch). Extend it to also
   deprecate any combination prompt that referenced the deleted row.
3. **Swap the Kinesis bridge for EventBridge Pipes.** Sketch how
   `kinesis_handler`'s decode-and-synthesize-REMOVE-event logic could be
   replaced by an EventBridge Pipes enrichment step, and what would change
   about retry/DLQ semantics.
4. **Make the TTL cleanup sweeper's scan efficient.** `dynamodb_ttl_cleanup`
   currently does a full table scan every run. Add a GSI keyed on
   `expiresAt` and rewrite it as a bounded `Query`.
5. **Add a timeout-wrapper test for a slow Langfuse call.** `prompt_sync.py`'s
   `run_with_timeout` wraps every Langfuse SDK call in a
   `ThreadPoolExecutor`-based timeout. Write a test that proves a call
   exceeding the timeout is reported as a failure without blocking the
   rest of the batch.
