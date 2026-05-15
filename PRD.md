# Persistent Context Engine — Project Requirements Document

### Hackathon: Anvil Problem 02 / Open Track

### Time Budget: 24 hours | Solo | No paid APIs | Beginner-friendly

---

## 0. Read This First

This document tells you exactly what to build, in what order, and why. Every decision
here is made to maximize your benchmark score within 24 hours on a laptop with no budget.

**There is no frontend.** The benchmark runs a Python script and produces a JSON score.
No judge opens a browser. Building a UI wastes 6-8 hours you cannot afford.

**The only thing that matters is the benchmark output.** Everything in this document
serves that goal.

---

## 1. What You Are Building — In Plain English

You are building a Python program that does two things:

**Thing 1: Remember everything**
It watches a stream of events happening across a software system — deployments,
errors, slow requests, alerts, and fixes. It stores all of them in a database,
tagged with permanent service identities that survive renames.

**Thing 2: Reconstruct context on demand**
When an incident (something breaking) is reported, the program instantly answers
four questions:

1. What events happened right before this broke?
2. What caused what?
3. Have we seen this pattern before?
4. What fixed it last time?

The hardest part: it must answer question 3 correctly even if the service was
renamed since the last incident. This is the central test of the benchmark.

---

## 2. What the Benchmark Actually Runs

The benchmark (which you do not have yet, but the problem statement describes fully)
works like this:

```
benchmark
  ├── calls Engine.__init__()          → your engine starts fresh
  ├── calls engine.ingest(events)      → feeds a stream of events
  ├── calls engine.reconstruct_context(signal, mode="fast")
  │                                    → expects a Context object back
  └── scores the Context object against ground truth
```

It runs this process many times with different random seeds. Each run starts
a completely fresh engine — no shared state between runs.

Your entire job is to make that Context object as accurate as possible, as fast
as possible.

---

## 3. The Exact Input Your Engine Receives

Events arrive as Python dicts (originally newline-delimited JSON). There are
exactly 6 guaranteed event types:

### 3.1 Deploy Event

```python
{
  "ts":      "2026-05-10T14:21:30Z",
  "kind":    "deploy",
  "service": "payments-svc",
  "version": "v2.14.0",
  "actor":   "ci"
}
```

Meaning: someone deployed a new version of a service.
Your engine must note: which service, which version, when.

### 3.2 Log Event

```python
{
  "ts":       "2026-05-10T14:22:01Z",
  "kind":     "log",
  "service":  "checkout-api",
  "level":    "error",
  "msg":      "timeout calling payments-svc",
  "trace_id": "abc123"
}
```

Meaning: a service emitted a log message, possibly an error.
Your engine must note: service, severity (error/warn/info), message.

### 3.3 Metric Event

```python
{
  "ts":      "2026-05-10T14:22:01Z",
  "kind":    "metric",
  "service": "payments-svc",
  "name":    "latency_p99_ms",
  "value":   4820
}
```

Meaning: a performance measurement was recorded.
Your engine must detect: is this value anomalously high? (latency > 1000ms = spike)

### 3.4 Trace Event

```python
{
  "ts":       "2026-05-10T14:22:08Z",
  "kind":     "trace",
  "trace_id": "abc123",
  "spans": [
    {"svc": "checkout-api",  "dur_ms": 5012},
    {"svc": "payments-svc",  "dur_ms": 4980}
  ]
}
```

Meaning: a request that passed through multiple services, with timing for each.
Your engine must note: which services are connected by this trace, slow spans.
Note: trace events have NO top-level "service" field — service names are inside spans.

### 3.5 Topology Event

```python
{
  "ts":     "2026-05-10T14:30:00Z",
  "kind":   "topology",
  "change": "rename",
  "from":   "payments-svc",
  "to":     "billing-svc"
}
```

Meaning: a service was renamed. THIS IS THE CRITICAL EVENT.
Your engine must: merge the old identity into the new one immediately.
All historical events under the old name must remain accessible via the new name.

### 3.6 Incident Signal

```python
{
  "ts":          "2026-05-10T14:32:11Z",
  "kind":        "incident_signal",
  "incident_id": "INC-714",
  "trigger":     "alert:checkout-api/error-rate>5%"
}
```

Meaning: an incident was declared. Something is broken.
Your engine must store this for later matching.

### 3.7 Remediation Event (bonus — implied by worked example)

```python
{
  "ts":          "2026-05-10T15:10:00Z",
  "kind":        "remediation",
  "incident_id": "INC-714",
  "action":      "rollback",
  "target":      "billing-svc",
  "version":     "v2.13.4",
  "outcome":     "resolved"
}
```

Meaning: someone took an action to fix the incident, and it worked.
Your engine must store this so it can suggest the same fix next time.

---

## 4. The Exact Output Your Engine Must Produce

The benchmark checks these fields by name. Spelling must match exactly.

```python
{
  "related_events": [
    # List of event dicts — the events that happened near this incident
    # Must be ordered chronologically
    # Must not contain duplicates
    { ...original event dict... },
    { ...original event dict... },
  ],

  "causal_chain": [
    # List of causal edges — what caused what
    {
      "cause_id":   "deploy:v2.14.0",        # string ID for the cause
      "effect_id":  "spike:latency_p99_ms",  # string ID for the effect
      "evidence":   "Deploy v2.14.0 preceded latency spike by 31 seconds",
      "confidence": 0.7                       # float between 0.0 and 1.0
    },
    # ... more edges
  ],

  "similar_past_incidents": [
    # List of past incidents that look like this one
    {
      "past_incident_id": "INC-301",
      "similarity":       0.85,           # float between 0.0 and 1.0
      "rationale":        "Same deploy+spike pattern, same trigger type"
    },
    # ... up to 5 results
  ],

  "suggested_remediations": [
    # List of actions that fixed similar incidents before
    {
      "action":             "rollback",
      "target":             "billing-svc",   # USE CURRENT NAME, not old name
      "historical_outcome": "resolved",
      "confidence":         0.72
    },
    # ... more suggestions
  ],

  "confidence": 0.75,   # float — overall confidence in this reconstruction

  "explain": "Incident context reconstructed from 4 related events. Deploy v2.14.0
              preceded a latency spike. Most similar past incident: INC-301 (similarity:
              0.85). Suggested fix: rollback billing-svc to prior version."
}
```

---

## 5. Performance Requirements (Hard Limits)

These are checked by the benchmark automatically. Failing any of them tanks your score.

| What                               | Requirement                      |
| ---------------------------------- | -------------------------------- |
| Ingest throughput                  | At least 1,000 events per second |
| Event → queryable lag              | Under 5 seconds                  |
| reconstruct_context (fast mode)    | p95 under 2 seconds              |
| reconstruct_context (deep mode)    | p95 under 6 seconds              |
| Cold start to first reconstruction | Under 60 seconds                 |

At L2 scale (the public benchmark — 12 services, 7 days, ~17,000 events), a
correctly written Python + DuckDB engine will comfortably hit all of these.
No optimisation trickery required.

---

## 6. Architecture — The Three Layers

Every component of this system lives in one of three layers.

```
┌─────────────────────────────────────────────────────────┐
│  LAYER 1: IDENTITY                                      │
│  engine/namebook.py                                     │
│                                                         │
│  "Who is who — and who were they before?"               │
│  payments-svc ──┐                                       │
│                 ├──► canonical ID: abc-123 (permanent)  │
│  billing-svc  ──┘                                       │
│                                                         │
│  This layer is the answer to the rename problem.        │
│  Everything else depends on it being correct.           │
└─────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────┐
│  LAYER 2: MEMORY                                        │
│  engine/memory.py                                       │
│                                                         │
│  "What happened, and when?"                             │
│  Stores every event tagged with canonical ID.           │
│  Stores every incident and its behavioral fingerprint.  │
│  Stores every remediation and its outcome.              │
│                                                         │
│  Database: DuckDB (in-process, no server needed)        │
└─────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────┐
│  LAYER 3: DETECTIVE                                     │
│  engine/detective.py                                    │
│                                                         │
│  "Does this look familiar? What should we do?"          │
│  At incident time: pulls related events, builds         │
│  causal chain, finds similar past incidents (by         │
│  behavioral pattern, NOT by service name),              │
│  suggests remediations.                                 │
└─────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────┐
│  ADAPTER                                                │
│  adapters/myteam.py                                     │
│                                                         │
│  Thin wrapper the benchmark talks to.                   │
│  Just routes calls to the three layers above.           │
└─────────────────────────────────────────────────────────┘
```

---

## 7. File Structure

```
sre-engine/
│
├── engine/
│   ├── __init__.py          empty file
│   ├── namebook.py          Layer 1: identity resolution
│   ├── memory.py            Layer 2: event storage (DuckDB)
│   ├── fingerprint.py       Layer 2b: behavioral pattern extraction
│   └── detective.py         Layer 3: context reconstruction
│
├── adapters/
│   ├── __init__.py          empty file
│   └── myteam.py            benchmark adapter (thin wrapper)
│
├── tests/
│   ├── test_rename.py       THE most important test — run first
│   ├── test_ingest.py       verify all 6 event kinds store correctly
│   └── test_reconstruct.py  verify context output shape and content
│
├── main.py                  manual test using the worked example
├── Dockerfile               required for submission
├── README.md                required for submission
└── requirements.txt         duckdb==0.10.3 (only dependency)
```

---

## 8. Component Specifications

### 8.1 NameBook (engine/namebook.py)

**Purpose:** Assign and maintain permanent IDs for services across their full
lifecycle, including renames.

**State it holds:**

- `name_to_id`: dict mapping any service name (current or historical) → permanent UUID
- `id_to_names`: dict mapping permanent UUID → list of all names, in order

**Methods:**

`lookup(service_name) → str`

- If name is known: return its permanent ID
- If name is new: generate a UUID, store it, return it
- Must never return different IDs for the same name across calls

`rename(old_name, new_name)`

- Calls lookup(old_name) to ensure old_name has an ID
- Points new_name to the SAME ID
- Appends new_name to the alias list for that ID
- Does NOT delete anything

`current_name(canonical_id) → str`

- Returns the most recent name for a canonical ID
- Used when building remediation suggestions (so we suggest "billing-svc" not "payments-svc")

`resolve_id_to_names(canonical_id) → list[str]`

- Returns all names ever used for this ID
- Used for building the explain narrative

**Critical invariants:**

- After rename("payments-svc", "billing-svc"):
  - lookup("payments-svc") == lookup("billing-svc") → MUST BE TRUE
  - current_name(lookup("billing-svc")) == "billing-svc" → MUST BE TRUE

**Test before proceeding to any other file:**

```python
book = NameBook()
id1 = book.lookup("payments-svc")
book.rename("payments-svc", "billing-svc")
id2 = book.lookup("billing-svc")
assert id1 == id2, "RENAME BROKE IDENTITY"
```

---

### 8.2 Memory (engine/memory.py)

**Purpose:** Persist all ingested events in a queryable store, tagged with
canonical IDs rather than service names.

**Database:** DuckDB, in-memory mode (`duckdb.connect(":memory:")`).
No files, no server, starts instantly.

**Tables:**

```sql
-- Every event that has ever been ingested
CREATE TABLE events (
    event_id     TEXT PRIMARY KEY,  -- md5 hash of event content
    happened_at  TEXT,              -- ISO timestamp string
    kind         TEXT,              -- deploy/log/metric/trace/incident_signal/remediation
    canonical_id TEXT,              -- permanent service ID (never changes on rename)
    service_name TEXT,              -- original name at time of event (may be old name)
    raw_json     TEXT               -- full original event as JSON string
);

-- Every incident signal received
CREATE TABLE incidents (
    incident_id   TEXT PRIMARY KEY,
    happened_at   TEXT,
    canonical_id  TEXT,
    trigger       TEXT,
    -- behavioral fingerprint fields (filled at ingest time):
    had_deploy    INTEGER DEFAULT 0,   -- 1 if a deploy preceded this incident
    had_spike     INTEGER DEFAULT 0,   -- 1 if a metric spike preceded this incident
    had_errors    INTEGER DEFAULT 0,   -- 1 if error logs preceded this incident
    deploy_gap_s  REAL    DEFAULT 0,   -- seconds between last deploy and incident
    error_types   TEXT    DEFAULT ''   -- space-separated error message prefixes
);

-- Every remediation event
CREATE TABLE remediations (
    incident_id   TEXT,
    action        TEXT,             -- rollback / restart / scale / etc.
    target_id     TEXT,             -- canonical ID of the target service
    version       TEXT,             -- version rolled back to (may be empty)
    outcome       TEXT,             -- resolved / escalated / unknown
    happened_at   TEXT
);
```

**Methods:**

`store_event(event: dict)`

- Route by event kind:
  - topology/rename → call namebook.rename(), return immediately (don't store)
  - all others → resolve service to canonical_id, insert into events table
  - incident_signal → also insert into incidents table
  - remediation → also insert into remediations table
- Generate event_id as md5(json.dumps(event, sort_keys=True))
- Use INSERT OR IGNORE to handle duplicates safely

`get_events_for_service(canonical_id, limit=50) → list[dict]`

- SELECT raw_json FROM events WHERE canonical_id = ? ORDER BY happened_at DESC LIMIT ?
- Return as list of parsed dicts

`get_events_in_window(canonical_id, ts, window_minutes=20) → list[dict]`

- Return events for a service within window_minutes of the given timestamp
- Since timestamps are ISO strings, sort in Python if DuckDB string comparison is unreliable

`get_all_past_incidents(exclude_id) → list[tuple]`

- SELECT incident_id, canonical_id, trigger, had_deploy, had_spike, had_errors, deploy_gap_s
  FROM incidents WHERE incident_id != ?

`get_remediations_for_incident(incident_id) → list[tuple]`

- SELECT action, target_id, version, outcome FROM remediations WHERE incident_id = ?

`update_incident_fingerprint(incident_id, had_deploy, had_spike, had_errors, deploy_gap_s)`

- UPDATE incidents SET had_deploy=?, had_spike=?, had_errors=?, deploy_gap_s=? WHERE incident_id=?
- Called after ingesting surrounding context when an incident_signal arrives

**Service name extraction by event kind:**

| Event kind      | How to get service name                                       |
| --------------- | ------------------------------------------------------------- |
| deploy          | event["service"]                                              |
| log             | event["service"]                                              |
| metric          | event["service"]                                              |
| trace           | event["spans"][0]["svc"] (first span)                         |
| incident_signal | parse from trigger: "alert:checkout-api/..." → "checkout-api" |
| remediation     | event["target"]                                               |
| topology        | event["from"] (handle rename, don't store)                    |

---

### 8.3 Fingerprint (engine/fingerprint.py)

**Purpose:** Describe an incident by its behavioral shape — the pattern of what
happened before it — so that similar incidents can be matched WITHOUT using
service names.

**What a fingerprint is:**
A dict of structured features extracted from the events that preceded an incident.
No embeddings. No vectors. Just structured comparison.

```python
fingerprint = {
    "trigger_type":  "alert",          # first segment of trigger string
    "trigger_metric": "error-rate",    # what metric triggered it
    "had_deploy":    True,             # was there a deploy in the last 30 min?
    "had_spike":     True,             # was there a latency spike > 1000ms?
    "had_errors":    True,             # were there error-level logs?
    "deploy_gap_s":  91,               # seconds from last deploy to incident (0 if no deploy)
    "event_kinds":   {"deploy", "log", "metric", "trace"},  # set of event kinds seen
}
```

**`extract_fingerprint(events, trigger) → dict`**

Given a list of related events and the incident trigger string, return a fingerprint.

```
trigger_type  = trigger.split(":")[0]          # "alert"
trigger_metric = extract_metric_name(trigger)   # "error-rate"
had_deploy    = any(e["kind"] == "deploy" for e in events)
had_spike     = any(e.get("value",0) > 1000 and "latency" in e.get("name","")
                    for e in events if e["kind"] == "metric")
had_errors    = any(e.get("level") == "error" for e in events if e["kind"] == "log")
event_kinds   = set(e["kind"] for e in events)

if had_deploy:
    deploy_ts = [e["ts"] for e in events if e["kind"] == "deploy"][-1]
    deploy_gap_s = (parse_ts(trigger_ts) - parse_ts(deploy_ts)).total_seconds()
else:
    deploy_gap_s = 0
```

**`similarity(fp1, fp2) → float`**

Returns a float 0.0–1.0 indicating how similar two fingerprints are.
No service names are used. Only behavioral features.

```
score = 0.0

# Trigger type match (30% weight) — alert vs alert, metric vs metric
if fp1["trigger_type"] == fp2["trigger_type"]:
    score += 0.30

# Both had deploys (25% weight)
if fp1["had_deploy"] == fp2["had_deploy"]:
    score += 0.25

# Both had spikes (20% weight)
if fp1["had_spike"] == fp2["had_spike"]:
    score += 0.20

# Both had errors (15% weight)
if fp1["had_errors"] == fp2["had_errors"]:
    score += 0.15

# Similar deploy-to-incident gap (10% weight)
# Full points if gaps are within 2x of each other
if fp1["deploy_gap_s"] > 0 and fp2["deploy_gap_s"] > 0:
    ratio = min(fp1["deploy_gap_s"], fp2["deploy_gap_s"]) \
          / max(fp1["deploy_gap_s"], fp2["deploy_gap_s"])
    score += 0.10 * ratio

return round(score, 3)
```

---

### 8.4 Detective (engine/detective.py)

**Purpose:** Given an incident signal, reconstruct the full context object that
the benchmark expects.

**`reconstruct(memory, signal, mode) → dict`**

This is the main function. Called by the adapter.

**Step-by-step logic:**

```
Step 1: Identify the service
  - Parse service name from signal["trigger"]
    "alert:checkout-api/error-rate>5%" → "checkout-api"
  - Resolve to canonical_id via memory.namebook.lookup()

Step 2: Get related events
  - Call memory.get_events_in_window(canonical_id, signal["ts"], window_minutes=20)
  - Also fetch events for services connected via traces (if any traces exist)
  - Deduplicate by event_id
  - Sort chronologically by ts

Step 3: Build causal chain
  - Find all deploys in related_events
  - Find all metric spikes in related_events (value > 1000ms for latency)
  - Find all error logs in related_events
  - For each deploy → spike pair (deploy must precede spike):
      add CausalEdge(deploy, spike, evidence, confidence=0.7)
  - For each deploy → error pair (deploy must precede error):
      add CausalEdge(deploy, error, evidence, confidence=0.6)
  - For each spike → incident pair:
      add CausalEdge(spike, incident, evidence, confidence=0.8)

Step 4: Extract fingerprint for THIS incident
  - Call fingerprint.extract_fingerprint(related_events, signal["trigger"])

Step 5: Find similar past incidents
  - Call memory.get_all_past_incidents(exclude_id=signal["incident_id"])
  - For each past incident, compute similarity(current_fp, past_fp)
  - Keep only those with similarity >= 0.4
  - Sort descending by similarity, return top 5
  - Format as list of {past_incident_id, similarity, rationale}

Step 6: Get suggested remediations
  - For each of the top 3 similar past incidents:
      fetch remediations from memory.get_remediations_for_incident()
      resolve target_id to CURRENT name via memory.namebook.current_name()
      weight confidence = similarity * (1.0 if outcome=="resolved" else 0.4)
  - Return list of {action, target, historical_outcome, confidence}

Step 7: Compute overall confidence
  - base: 0.2
  - +0.2 if related_events is non-empty
  - +0.2 if causal_chain has at least one edge
  - +0.2 if similar_past_incidents is non-empty
  - +0.2 if suggested_remediations is non-empty
  - cap at 1.0

Step 8: Write explanation
  - Template string using the data above
  - Must be a coherent English sentence, not bullet points
  - Fast mode: template only (no LLM)
  - Deep mode: template + "Extended analysis would go here" (LLM optional)

Step 9: Return Context dict
  - Field names must match schema.py EXACTLY when repo is available
```

---

### 8.5 Adapter (adapters/myteam.py)

**Purpose:** The file the benchmark imports. Must implement `ingest`,
`reconstruct_context`, and `close`.

**Critical rules:**

- ALL state must live inside `self` (no module-level globals)
- `__init__` must create a completely fresh engine every time it is called
- `close` must call `self.memory.db.close()`
- Each seed in the benchmark creates a new Engine() — isolation is mandatory

```python
from engine.memory import Memory
from engine.detective import reconstruct

class Engine:
    def __init__(self):
        self.memory = Memory()   # fresh DuckDB instance

    def ingest(self, events):
        for event in events:
            self.memory.store_event(event)

    def reconstruct_context(self, signal, mode="fast"):
        return reconstruct(self.memory, signal, mode=mode)

    def close(self):
        self.memory.db.close()
```

This file should be fewer than 20 lines. Any logic that ends up here is in
the wrong place.

---

## 9. Testing Strategy

### 9.1 Test 1: Rename Identity (Run This First)

File: `tests/test_rename.py`

```python
from engine.namebook import NameBook

def run():
    book = NameBook()
    id_before = book.lookup("payments-svc")
    book.rename("payments-svc", "billing-svc")
    id_after  = book.lookup("billing-svc")

    assert id_before == id_after, "FAIL: rename broke identity"
    assert book.current_name(id_after) == "billing-svc", "FAIL: current_name wrong"
    print("PASS: rename preserves identity")

run()
```

Run with: `python tests/test_rename.py`
Do not proceed to the next file until this prints PASS.

### 9.2 Test 2: Ingest All Event Types

File: `tests/test_ingest.py`

Ingest one of each event kind. After ingestion:

- events table should have entries for deploy, log, metric, trace, incident_signal
- topology/rename should NOT appear in events table
- incidents table should have one row
- all rows should have the correct canonical_id (not the raw service name)

### 9.3 Test 3: Worked Example End-to-End

File: `tests/test_reconstruct.py`

Use the exact 7-event sequence from the problem statement.
After ingesting all 7 events and calling reconstruct_context on INC-714:

- related_events: must contain the deploy, log, metric, and trace events
- causal_chain: must contain at least one edge with confidence >= 0.5
- similar_past_incidents: this will be EMPTY for a single-incident run —
  that is correct. The benchmark tests this by ingesting TRAINING incidents first.
- suggested_remediations: empty if no similar incidents found
- confidence: should be > 0.4 since we have events and a causal chain
- explain: must be a non-empty string

### 9.4 Test 4: Cross-Rename Recall (The Real Test)

This test simulates what the benchmark actually checks.

```
Ingest scenario:
  INC-001: payments-svc v2.13.0 deploy → spike → errors → incident → rollback (resolved)

Then:
  topology: rename payments-svc → billing-svc

Then:
  INC-002: billing-svc v2.15.0 deploy → spike → errors → incident

Call reconstruct_context on INC-002.
Assert that INC-001 appears in similar_past_incidents with similarity > 0.4.
```

If this test fails, the benchmark will fail. This is the single most important
test in the project.

---

## 10. Build Order (Hour by Hour)

This is the order that minimises risk. Do not skip steps.

### Hours 0–1: Environment and skeleton

- Create folder structure
- Create all empty files
- Run `pip install duckdb`
- Verify Python version is 3.9+

### Hours 1–3: NameBook

- Write engine/namebook.py
- Write tests/test_rename.py
- Run test, fix until PASS
- Do not move on until PASS

### Hours 3–6: Memory

- Write engine/memory.py
- Write tests/test_ingest.py
- Ingest the 7 worked-example events manually and query the DB in a REPL
- Verify events table has correct canonical_ids
- Verify topology events don't appear in events table

### Hours 6–8: Fingerprint

- Write engine/fingerprint.py
- Test extract_fingerprint on the worked example events
- Test similarity() on two similar fingerprints — should return > 0.4
- Test similarity() on two different fingerprints — should return < 0.3

### Hours 8–12: Detective

- Write engine/detective.py
- Write tests/test_reconstruct.py
- Run end-to-end on worked example
- Then run the cross-rename test (Test 4 above)
- Iterate until the cross-rename test passes

### Hours 12–13: Adapter

- Write adapters/myteam.py (10–15 lines)
- Run main.py and verify full output shape

### Hours 13–16: Benchmark Integration (when repo is available)

- Clone the repo
- Read schema.py — compare field names against your Context output dict
- Fix any mismatches
- Run `python self_check.py --adapter adapters.myteam:Engine --quick`
- Fix whatever fails

### Hours 16–20: Iteration

- Run full battery: `python run.py --adapter adapters.myteam:Engine --seeds 9999 31415 27182`
- Look at which metrics score lowest
- Fix the weakest axis first (likely: similarity threshold tuning)

### Hours 20–22: Writeup

- Write the 3-page PDF (see Section 12)
- Record the 5-minute demo (see Section 13)

### Hours 22–24: Submission

- Write Dockerfile (see Section 14)
- Write README (see Section 15)
- Push to GitHub
- Submit link

---

## 11. Scoring — What the Benchmark Measures and How to Maximise Each

### 11.1 Incident Recall — precision@5 and recall@5

**What it measures:** For a given incident, does your engine surface the correct
historically similar incidents, ranked in the top 5?

**What hurts you:**

- Matching by service name (fails the rename test)
- Similarity threshold too high (misses valid matches)
- Similarity threshold too low (returns noise, hurts precision)

**How to improve:** Start with threshold = 0.4. After seeing self_check output,
adjust up if precision is low, down if recall is low.

### 11.2 Context Quality — F1 on related_events

**What it measures:** Do your related_events contain the right events?
Ground truth will include the deploy, the error logs, the metric spike, and the trace.

**What hurts you:**

- Window too narrow (missing relevant events)
- Window too wide (including irrelevant events)
- Not fetching events for connected services (via traces)

**How to improve:** Use a 20-minute window. Also fetch events for services
that appear in trace spans near the incident time.

### 11.3 Pattern Recognition — F1 on incident family classification

**What it measures:** Can your engine classify incidents into families
(deploy-caused vs resource-exhaustion vs dependency-failure)?

**How to improve:** The fingerprint features implicitly do this. If had_deploy=True
and had_spike=True, that's the "bad deploy" family. The similarity function
naturally groups incidents by family.

### 11.4 Temporal Reasoning — correct causal ordering

**What it measures:** In your causal_chain, does cause always precede effect
in timestamps?

**Critical rule:** Before adding a CausalEdge(cause, effect), verify that
the cause event's timestamp is earlier than the effect event's timestamp.
Never add an edge where effect precedes cause.

### 11.5 Adaptability — Δ-metric pre-drift vs post-drift

**What it measures:** How much does your recall drop after a service rename?

**If your NameBook is correct:** This metric should be near zero. The whole
point of canonical IDs is that drift doesn't affect matching.

### 11.6 Latency — p95 timings

**At L2 scale (17k events, 12 services):** DuckDB in-memory is fast enough
with no optimisation. Expect ~50ms for fast-mode reconstruction.

**If you're hitting latency issues:** Add a simple dict cache for canonical_id
lookups. Avoid loading all events into Python lists — use SQL WHERE clauses
to filter in the database.

### 11.7 Memory Evolution — improvement after full ingestion

**What it measures:** Do your metrics improve as more remediation data is ingested?

**How this works in your engine:** The more resolved remediations are stored,
the better the suggested_remediations. This happens automatically if your
storage is correct.

### 11.8 Explainability — judge-graded 1–5

**What judges look for:**

- Clear explanation of what happened
- References the causal chain (e.g., "Deploy v2.14.0 preceded latency spike")
- References past incidents (e.g., "Similar to INC-301 from 3 days ago")
- References the suggested fix
- Readable English, not JSON

**Your explain template should cover all four of these.**

---

## 12. Writeup (3-Page PDF)

Required for submission. Judges read this before the Q&A.
Structure it to pre-answer the questions they will ask.

**Page 1: Memory Representation and Relationship Synthesis**

Explain:

- The canonical ID system and how it solves the rename problem
- The three-table DuckDB schema (events, incidents, remediations)
- How events are tagged at ingest time (not at query time)
- Why this choice: no vector database, no LLM at ingest time, just structured storage

**Page 2: Drift Handling and Latency Engineering**

Explain:

- What happens when a topology/rename event arrives (NameBook.rename())
- How historical events remain queryable under new names (canonical_id lookup)
- The fingerprint approach: why structured features beat embeddings for this problem
- Latency: DuckDB in-process, no network calls in fast mode

**Page 3: Evolution Mechanism and Known Limitations**

Explain:

- How the engine improves as more incidents and remediations are ingested
- Confidence decay is implicit: more training data → better fingerprint matching
- Known limitations: similarity threshold is manually tuned, not learned
- What you would build next given more time (learned thresholds, LLM-enhanced explain)

---

## 13. Demo (5-Minute Screen Recording)

Script:

**0:00–0:30** — Show the worked example JSONL being ingested
**0:30–1:00** — Show the rename event arriving, show NameBook state before and after
**1:00–2:30** — Show reconstruct_context output for INC-714

- Point to related_events: "These 4 events are what happened"
- Point to causal_chain: "This is what caused what"
  **2:30–4:00** — Show the cross-rename test
- Ingest a past incident under payments-svc
- Rename to billing-svc
- Trigger new incident under billing-svc
- Show that the past incident surfaces in similar_past_incidents
- This is the money shot. Spend 90 seconds here.
  **4:00–5:00** — Show self_check output with scores

---

## 14. Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

`requirements.txt`:

```
duckdb==0.10.3
```

Build: `docker build -t sre-engine .`
Run: `docker run sre-engine`

---

## 15. README (Required)

```markdown
# Persistent Context Engine

## What it does

Operational memory substrate for SRE. Ingests telemetry streams, builds
persistent behavioral memory, reconstructs incident context on demand —
topology-independent.

## Quick Start (3 commands)

git clone <your-repo>
cd sre-engine
pip install -r requirements.txt && python main.py

## Run the benchmark adapter

python self_check.py --adapter adapters.myteam:Engine --quick

## Architecture

Three layers: Identity (NameBook), Memory (DuckDB), Detective (fingerprint matching).
See writeup.pdf for full design rationale.

## Dependencies

- duckdb==0.10.3 (only external dependency)
- Python 3.9+
- No paid APIs. No network calls at runtime.
```

---

## 16. What NOT to Build

These will cost you time and earn you nothing on the benchmark:

- **Any frontend or dashboard** — not evaluated, not scored
- **A REST API or web server** — the benchmark talks to a Python class directly
- **Kafka or any streaming infrastructure** — ingest() receives a Python iterable
- **Vector embeddings** — DuckDB + structured fingerprints is faster, simpler, and sufficient
- **An LLM at ingest time** — too slow, fails the 1000 events/sec throughput requirement
- **Redis or any external cache** — DuckDB in-memory IS the cache
- **Async/await** — the benchmark interface is synchronous; async adds complexity with no benefit
- **A "nice" README with badges and screenshots** — judges care about the PDF writeup, not the README

---

## 17. The Questions Judges Will Ask (and Your Answers)

**"How does your system handle service renames?"**
"Every service is assigned a permanent canonical ID on first contact. Rename events
update an alias table but never change the ID. All historical events are stored
under the canonical ID, so after a rename, all history remains accessible under
the new name."

**"Why DuckDB instead of Neo4j or a vector database?"**
"At L2 scale, the bottleneck is not graph traversal — it's ingest throughput.
DuckDB runs in-process with no serialisation overhead, hits 1000+ events/sec
trivially, and SQL window queries are sufficient for temporal reasoning.
A graph database adds setup complexity and network latency for no measurable benefit
at this scale."

**"What specifically fails in the baseline?"**
"The baseline uses vector similarity on event text. Service names are part of that
text. After a rename, 'payments-svc' and 'billing-svc' have low vector similarity
despite being the same service. Our approach strips service names out of the
matching signal entirely — only behavioral features are compared."

**"How does your engine improve over time?"**
"As more incidents are ingested and their remediations recorded, the similarity
search has more training data to draw from. An incident with one historical match
gets lower confidence than one with ten matches pointing to the same remediation.
Confidence naturally increases with data."

---

## 18. Risk Register

| Risk                                               | Probability | Impact                                 | Mitigation                                    |
| -------------------------------------------------- | ----------- | -------------------------------------- | --------------------------------------------- |
| Schema field names don't match benchmark schema.py | High        | Kills automated score                  | First thing to check when repo is available   |
| Global state leaks between seeds                   | Medium      | Inflated local score, fails multi-seed | Every piece of state inside **init**          |
| Fingerprint similarity too coarse                  | Medium      | Low recall@5                           | Tune threshold after seeing self_check output |
| Timestamp parsing edge cases                       | Low         | Broken causal ordering                 | Use Python's datetime.fromisoformat()         |
| Rename arrives mid-reconstruction                  | Low         | Stale ID used                          | NameBook uses a threading.RLock               |
| DuckDB version incompatibility                     | Low         | Import errors                          | Pin to duckdb==0.10.3 in requirements.txt     |

---

_This document covers everything required for a passing submission. Build in order.
Test at every step. The rename test is the gate — nothing else matters until it passes._
