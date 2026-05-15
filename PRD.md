# Persistent Context Engine — Project Requirements Document v3

### Hackathon: Anvil Problem 02 / Open Track

### Time Budget: 24 hours | Solo | No paid APIs | Beginner-friendly

---

## 0. Read This First

This document supersedes v2 entirely. It is produced by cross-referencing every
line of the public benchmark harness (schema.py, generator.py, harness.py,
metrics.py, adapter.py, adapters/dummy.py) against the v2 PRD.

**Nine bugs are fixed. Three are score-killing. Read §0.1 before anything else.**

There is no frontend. The benchmark runs a Python script and produces a JSON score.
No judge opens a browser. Building a UI wastes 6–8 hours you cannot afford.

The only thing that matters is the benchmark output.

---

## 0.1 Bug Inventory — v2 → v3

| #   | v2 Bug                                                                        | Severity     | Score Impact                                                            | Fix in v3                                                   |
| --- | ----------------------------------------------------------------------------- | ------------ | ----------------------------------------------------------------------- | ----------------------------------------------------------- |
| B1  | `IncidentMatch` key `past_incident_id`                                        | **CRITICAL** | recall@5 + precision@5_mean = 0 (45% of score)                          | Key is `incident_id` per schema.py and metrics.py           |
| B2  | `CausalEdge` keys `cause_id` / `effect_id`                                    | **CRITICAL** | Schema mismatch; context quality panel score collapses                  | Keys are `cause_event_id` / `effect_event_id` per schema.py |
| B3  | Topology field `event["from"]`                                                | **CRITICAL** | All renames fail silently → cross-rename recall = 0                     | Field is `event["from_"]` per generator.py and schema.py    |
| B4  | Detective ignores `signal["service"]`                                         | HIGH         | Fragile trigger parsing causes empty context on novel formats           | Read `signal.get("service")` directly — always present      |
| B5  | `dep_add` / `dep_remove` topology not handled                                 | HIGH         | Engine crashes or silently corrupts on 40% of topology events           | Route to no-op, do not crash                                |
| B6  | `similar_past_incidents` dict still uses `past_incident_id` in Detective code | HIGH         | Cascades from B1 — the field is produced wrong even after fixing schema | Fix construction site in detective.py                       |
| B7  | Split handler uses `event["into"]` — field absent in schema                   | MEDIUM       | No splits in L2 generator; L3 unknown                                   | Use `event.get("into", [])` defensively                     |
| B8  | Split/merge topology uses `event["from"]` (same as B3)                        | MEDIUM       | Cascades from B3                                                        | `event["from_"]` everywhere                                 |
| B9  | No graceful fallback when `similar_past_incidents` is empty in scoring string | LOW          | KeyError on first run                                                   | Guard with `if scored` before `scored[0]`                   |

---

## 0.2 How the Benchmark Actually Scores You

Read this before writing a single line of code. It tells you exactly which bugs
kill the most points.

```
Axis                Weight  Source          What it needs from you
─────────────────────────────────────────────────────────────────────
recall@5            0.30    Automated       similar_past_incidents[*]["incident_id"]
                                            ends with "-{family_int}"
precision@5_mean    0.15    Automated       same field, precision within top-5
remediation_acc     0.20    Automated       suggested_remediations[*]["action"] == "rollback"
latency_p95_ms      0.15    Automated       reconstruct_context p95 ≤ 2000ms
manual_context      0.10    Panel           related_events quality + explain
manual_explain      0.10    Panel           explain field narrative
```

The `recall@5` and `precision@5_mean` axes together are 45% of the automated
score. Both depend entirely on `similar_past_incidents[*]["incident_id"]`.
Bug B1 (wrong key name) alone zeros out 45% of your score. Fix B1 first.

Family matching: the benchmark extracts the family integer by doing
`incident_id.rsplit("-", 1)[-1]` and parsing as int. Your training incidents
are stored with IDs like `INC-738291-3` (family 3). Your `similar_past_incidents`
must return those exact IDs under the `incident_id` key.

---

## 1. What You Are Building — In Plain English

(Unchanged from v2 — see that section.)

---

## 2. What the Benchmark Actually Runs

```
benchmark
  ├── adapter = Engine()              → fresh instance per seed
  ├── adapter.ingest(train_events)    → all training telemetry
  ├── adapter.ingest(eval_events)     → eval context events (NO remediations)
  ├── for each eval signal:
  │     ctx = adapter.reconstruct_context(signal, mode="fast")
  │     score_match(ctx, ground_truth, k=5)      → recall@5, precision@5
  │     score_remediation(ctx, ground_truth)      → remediation_acc
  └── adapter.close()
```

Both `train_events` and `eval_events` are ingested before any query. The engine
sees all pre-signal context for eval incidents. Remediations for eval incidents
are NOT ingested — the engine must predict them from training history.

Each seed runs in a **freshly constructed** adapter. No state leaks between seeds.

---

## 3. The Exact Input Your Engine Receives

### Critical: Field name `from_` not `from`

Python's `from` is a reserved keyword. The benchmark uses `from_` everywhere a
topology event source is named. This applies to renames, dep_add, and dep_remove.

### 3.1 Deploy Event

```python
{"ts": "2026-05-10T14:21:30Z", "kind": "deploy",
 "service": "payments-svc", "version": "v2.14.0", "actor": "ci"}
```

### 3.2 Log Event

```python
{"ts": "2026-05-10T14:22:01Z", "kind": "log",
 "service": "checkout-api", "level": "error",
 "msg": "timeout calling payments-svc", "trace_id": "abc123"}
```

### 3.3 Metric Event

```python
{"ts": "2026-05-10T14:22:01Z", "kind": "metric",
 "service": "payments-svc", "name": "latency_p99_ms", "value": 4820}
```

Background metrics have `name: "qps"` and values 10–1000. Your spike detector
uses `value > 1000 AND "latency" in name`, so background events are safely
ignored by existing logic.

### 3.4 Trace Event

```python
{"ts": "2026-05-10T14:22:08Z", "kind": "trace",
 "trace_id": "abc123",
 "spans": [{"svc": "checkout-api", "dur_ms": 5012},
            {"svc": "payments-svc",  "dur_ms": 4980}]}
```

No top-level `service` field. Names live inside spans.

### 3.5 Topology — Rename (FIELD IS `from_` NOT `from`)

```python
{"ts": "2026-05-10T14:30:00Z", "kind": "topology",
 "change": "rename", "from_": "payments-svc", "to": "billing-svc"}
```

### 3.6 Topology — dep_add / dep_remove (NEW — must handle gracefully)

```python
{"ts": "2026-05-10T14:30:00Z", "kind": "topology",
 "change": "dep_add", "from_": "checkout-api", "to": "payments-svc"}

{"ts": "2026-05-10T14:30:00Z", "kind": "topology",
 "change": "dep_remove", "from_": "checkout-api", "to": "payments-svc"}
```

These are dependency-graph mutations, not identity mutations. They do NOT affect
the TemporalIdentityGraph. Route them to a no-op. Do NOT crash.

In the generator, topology mutations are: 60% rename, 20% dep_add, 20% dep_remove.
At L2 (8 topology mutations), expect ~5 renames and ~3 dep_add/dep_remove.

### 3.7 Topology — Split (rare; not in L2 generator but handle defensively)

```python
{"ts": "...", "kind": "topology", "change": "split",
 "from_": "payments-svc", "into": ["billing-svc", "refunds-svc"]}
```

### 3.8 Topology — Merge (rare; not in L2 generator but handle defensively)

```python
{"ts": "...", "kind": "topology", "change": "merge",
 "from_": ["payments-svc", "billing-svc"], "into": "finance-svc"}
```

### 3.9 Incident Signal

```python
{"ts": "2026-05-10T14:32:11Z", "kind": "incident_signal",
 "incident_id": "INC-738291-3",       # ends with "-{family_int}"
 "trigger": "alert:checkout-api/latency_p99_ms>3000",
 "service": "checkout-api"}           # ALWAYS PRESENT — use this directly
```

The harness always passes `service` in the signal. Use it. Do not depend on
trigger parsing as your primary service-name source.

Incident ID format from generator: `INC-{int(t.timestamp()) % 100000}-{family_int}`
The `-{family_int}` suffix is what the scoring uses to determine family membership.

### 3.10 Remediation Event

```python
{"ts": "2026-05-10T15:10:00Z", "kind": "remediation",
 "incident_id": "INC-738291-3", "action": "rollback",
 "target": "billing-svc", "version": "v2.13.4", "outcome": "resolved"}
```

The expected remediation action in ALL benchmark scenarios is `"rollback"`.
`remediation_acc` checks `action == "rollback"`. Your suggestions must include
this action.

---

## 4. The Exact Output Your Engine Must Produce

**Field names are checked by the benchmark. Spelling kills points silently.**

```python
{
  "related_events": [
    # Ordered chronologically, no duplicates, original event dicts
    { ...event dict... },
  ],

  "causal_chain": [
    {
      "cause_event_id": "deploy:v2.14.0",       # KEY IS cause_event_id NOT cause_id
      "effect_event_id": "spike:latency_p99_ms", # KEY IS effect_event_id NOT effect_id
      "evidence":   "Deploy v2.14.0 preceded latency spike by 31s",
      "confidence": 0.82
    },
  ],

  "similar_past_incidents": [
    {
      "incident_id": "INC-738291-3",   # KEY IS incident_id NOT past_incident_id
      "similarity":  0.85,
      "rationale":   "Same deploy+spike pattern, same trigger type, matching motif"
    },
  ],

  "suggested_remediations": [
    {
      "action":             "rollback",          # must be "rollback" for scoring
      "target":             "billing-svc",
      "historical_outcome": "resolved",
      "confidence":         0.72
    },
  ],

  "confidence": 0.75,

  "explain": "Incident context reconstructed from 4 related events. ..."
}
```

### Schema cross-reference (schema.py)

```python
class CausalEdge(TypedDict, total=False):
    cause_event_id: str    # ← NOT cause_id
    effect_event_id: str   # ← NOT effect_id
    evidence: str
    confidence: float

class IncidentMatch(TypedDict, total=False):
    incident_id: str       # ← NOT past_incident_id
    similarity: float
    rationale: str

class Remediation(TypedDict, total=False):
    action: str
    target: str
    historical_outcome: str
    confidence: float
```

---

## 5. Performance Requirements (Hard Limits)

| What                               | Requirement        |
| ---------------------------------- | ------------------ |
| Ingest throughput                  | ≥ 1,000 events/sec |
| Event → queryable lag              | ≤ 5 seconds        |
| reconstruct_context (fast mode)    | p95 ≤ 2 seconds    |
| reconstruct_context (deep mode)    | p95 ≤ 6 seconds    |
| Cold start to first reconstruction | ≤ 60 seconds       |

Latency is scored as `min(1.0, 2000 / p95_ms)`. At p95 = 500ms you score 1.0.
At p95 = 4000ms you score 0.5. The latency axis is 15% of total.

---

## 6. Architecture — Four Layers

(Same as v2 — structure unchanged.)

---

## 7. File Structure

```
sre-engine/
│
├── engine/
│   ├── __init__.py
│   ├── temporal_identity_graph.py   Layer 1: identity across time
│   ├── memory.py                    Layer 2: event storage (DuckDB)
│   ├── fingerprint.py               Layer 3a: behavioral feature extraction
│   ├── motif.py                     Layer 3b: structural motif extraction
│   └── detective.py                 Layer 4: context reconstruction
│
├── adapters/
│   ├── __init__.py
│   └── myteam.py
│
├── tests/
│   ├── test_rename.py
│   ├── test_split_merge.py
│   ├── test_ingest.py
│   ├── test_motif.py
│   └── test_reconstruct.py
│
├── main.py
├── Dockerfile
├── README.md
└── requirements.txt
```

---

## 8. Component Specifications

---

### 8.1 TemporalIdentityGraph (engine/temporal_identity_graph.py)

Unchanged from v2 except:

- All callers must pass `event["from_"]` (not `event["from"]`) when processing topology events.
- `dep_add` and `dep_remove` events must never reach this class. Route them to a no-op in Memory.

The invariant tests from v2 are correct and unchanged. Pass them before proceeding.

```python
# Data structures (unchanged)
@dataclass
class IdentityNode:
    canonical_id: str
    name:         str
    active_from:  str
    active_until: str | None

@dataclass
class IdentityEdge:
    from_id: str
    to_id:   str
    kind:    str
    ts:      str
```

All method signatures and implementations are correct in v2. The only fix needed
is in callers (Memory.store*event) that extract `event["from"]` — change to
`event["from*"]`.

---

### 8.2 Memory (engine/memory.py)

#### Fixed: Topology routing in store_event

```python
def store_event(self, event: dict):
    kind = event.get("kind")

    if kind == "topology":
        change = event.get("change")
        ts = event["ts"]

        if change == "rename":
            # FIX B3/B8: field is "from_" not "from"
            self.tig.rename(event["from_"], event["to"], ts)

        elif change == "split":
            # FIX B7: "into" may not exist — defensive get
            old_name = event.get("from_", "")
            new_names = event.get("into", [])
            if old_name and new_names:
                self.tig.split(old_name, new_names, ts)

        elif change == "merge":
            old_names = event.get("from_", [])
            new_name  = event.get("into", "")
            if old_names and new_name:
                self.tig.merge(old_names, new_name, ts)

        elif change in ("dep_add", "dep_remove"):
            # FIX B5: no-op — dependency edges are not identity mutations
            pass

        else:
            # Unknown topology change — silently ignore, do not crash
            pass

        return  # topology events are never stored in events table

    # ... rest of store_event unchanged
```

#### Tables (unchanged from v2)

```sql
CREATE TABLE events (
    event_id     TEXT PRIMARY KEY,
    happened_at  TEXT,
    kind         TEXT,
    canonical_id TEXT,
    service_name TEXT,
    raw_json     TEXT
);

CREATE TABLE incidents (
    incident_id      TEXT PRIMARY KEY,
    happened_at      TEXT,
    canonical_id     TEXT,
    trigger          TEXT,
    trigger_type     TEXT,
    trigger_metric   TEXT,
    fingerprint_json TEXT,
    causal_json      TEXT
);

CREATE TABLE remediations (
    incident_id  TEXT,
    action       TEXT,
    target_id    TEXT,
    version      TEXT,
    outcome      TEXT,
    happened_at  TEXT
);
```

All other Memory methods are correct in v2. No changes needed.

---

### 8.3 Fingerprint (engine/fingerprint.py)

Unchanged from v2. All function signatures and implementations are correct.

`parse_trigger` correctly handles the generator's trigger format
`"alert:{svc}/latency_p99_ms>3000"` → `("alert", "latency_p99_ms")`.

`extract_service_name_from_trigger` is retained as a fallback only. Primary
service name comes from `signal["service"]` in Detective (see §8.5).

---

### 8.4 Structural Motif Similarity (engine/motif.py)

Unchanged from v2. All function signatures and implementations are correct.

---

### 8.5 Detective (engine/detective.py)

#### Fixed Step 1: Use signal["service"] directly

```python
def reconstruct(memory, signal, mode="fast"):
    trigger = signal.get("trigger", "")
    incident_id = signal["incident_id"]

    # FIX B4: signal["service"] is ALWAYS present from the harness.
    # Use it directly. Fall back to trigger parsing only as a safety net.
    svc_name = signal.get("service") or extract_service_name_from_trigger(trigger)

    if svc_name:
        canonical_id = memory.tig.lookup(svc_name, at_time=signal["ts"])
    else:
        canonical_id = None
```

#### Steps 2–4: Related events, causal chain, fingerprint

These are identical to v2. No changes.

#### Fixed Step 5: similar_past_incidents — key is `incident_id` not `past_incident_id`

```python
past_incidents = memory.get_all_past_incidents(exclude_id=incident_id)

scored = []
for past in past_incidents:
    fp_past    = past["fingerprint"]
    motif_past = extract_motif(past["causal_chain"])

    sim = combined_similarity(fp_current, fp_past, motif_current, motif_past)

    if sim >= 0.35:
        motif_name = get_motif_name(motif_current)
        scored.append({
            # FIX B1/B6: key is "incident_id" not "past_incident_id"
            "incident_id": past["incident_id"],
            "similarity":  sim,
            "rationale": (
                f"Matched via fingerprint+motif ({motif_name}). "
                f"trigger_type={'match' if fp_current.get('trigger_type')==fp_past.get('trigger_type') else 'diff'}, "
                f"had_deploy={fp_current.get('had_deploy')}/{fp_past.get('had_deploy')}, "
                f"had_spike={fp_current.get('had_spike')}/{fp_past.get('had_spike')}"
            )
        })

scored.sort(key=lambda x: x["similarity"], reverse=True)
similar_past_incidents = scored[:5]
```

#### Fixed Step 3: Causal chain field names

```python
causal_chain = []

for deploy in deploys:
    for spike in spikes:
        if deploy["ts"] < spike["ts"]:
            gap_s = (datetime.fromisoformat(spike["ts"].replace("Z","+00:00")) -
                     datetime.fromisoformat(deploy["ts"].replace("Z","+00:00"))
                    ).total_seconds()
            conf = temporal_confidence(deploy["ts"], spike["ts"], base=0.85)
            causal_chain.append({
                # FIX B2: keys are cause_event_id / effect_event_id
                "cause_event_id":  f"deploy:{deploy.get('version','unknown')}",
                "effect_event_id": f"spike:{spike.get('name','metric')}",
                "evidence":        f"Deploy {deploy.get('version')} preceded "
                                   f"latency spike by {gap_s:.0f}s",
                "confidence":      conf
            })

    for error in errors:
        if deploy["ts"] < error["ts"]:
            conf = temporal_confidence(deploy["ts"], error["ts"], base=0.75)
            causal_chain.append({
                "cause_event_id":  f"deploy:{deploy.get('version','unknown')}",
                "effect_event_id": f"error:{error.get('msg','')[:40]}",
                "evidence":        f"Deploy {deploy.get('version')} preceded error log",
                "confidence":      conf
            })

for spike in spikes:
    if spike["ts"] <= signal["ts"]:
        conf = temporal_confidence(spike["ts"], signal["ts"], base=0.90)
        causal_chain.append({
            "cause_event_id":  f"spike:{spike.get('name','metric')}",
            "effect_event_id": f"incident:{signal['incident_id']}",
            "evidence":        "Metric spike preceded incident declaration",
            "confidence":      conf
        })
```

#### Fixed Step 9: Guard before indexing scored list

```python
# FIX B9: guard before indexing
top_match = similar_past_incidents[0] if similar_past_incidents else None
top_rem   = suggested_remediations[0] if suggested_remediations else None
```

#### Full reconstruct function — corrected signature

```python
def reconstruct(memory, signal, mode="fast") -> dict:
    trigger     = signal.get("trigger", "")
    incident_id = signal["incident_id"]

    # Step 1: identify service
    svc_name = signal.get("service") or extract_service_name_from_trigger(trigger)
    if svc_name:
        canonical_id = memory.tig.lookup(svc_name, at_time=signal["ts"])
    else:
        canonical_id = None

    # Step 2: related events with trace fan-out
    if canonical_id:
        initial_events = memory.get_events_in_window(
            canonical_id, signal["ts"], window_minutes=20)
    else:
        initial_events = []

    trace_events = [e for e in initial_events if e["kind"] == "trace"]
    connected_svc_names = set()
    for trace in trace_events:
        for span in trace.get("spans", []):
            svc = span.get("svc")
            if svc and svc != svc_name:
                connected_svc_names.add(svc)

    seen_event_ids = {
        hashlib.md5(json.dumps(e, sort_keys=True).encode()).hexdigest()
        for e in initial_events
    }
    for connected_svc in connected_svc_names:
        connected_id = memory.tig.lookup(connected_svc, at_time=signal["ts"])
        for e in memory.get_events_in_window(
                connected_id, signal["ts"], window_minutes=20):
            eid = hashlib.md5(json.dumps(e, sort_keys=True).encode()).hexdigest()
            if eid not in seen_event_ids:
                initial_events.append(e)
                seen_event_ids.add(eid)

    related_events = sorted(initial_events, key=lambda e: e["ts"])

    # Step 3: causal chain (cause_event_id / effect_event_id)
    deploys = [e for e in related_events if e["kind"] == "deploy"]
    spikes  = [e for e in related_events
               if e["kind"] == "metric"
               and e.get("value", 0) > 1000
               and "latency" in e.get("name", "")]
    errors  = [e for e in related_events
               if e["kind"] == "log" and e.get("level") == "error"]

    causal_chain = []
    for deploy in deploys:
        for spike in spikes:
            if deploy["ts"] < spike["ts"]:
                gap_s = (datetime.fromisoformat(spike["ts"].replace("Z","+00:00")) -
                         datetime.fromisoformat(deploy["ts"].replace("Z","+00:00"))
                        ).total_seconds()
                conf = temporal_confidence(deploy["ts"], spike["ts"], base=0.85)
                causal_chain.append({
                    "cause_event_id":  f"deploy:{deploy.get('version','unknown')}",
                    "effect_event_id": f"spike:{spike.get('name','metric')}",
                    "evidence":  f"Deploy {deploy.get('version')} preceded spike by {gap_s:.0f}s",
                    "confidence": conf
                })
        for error in errors:
            if deploy["ts"] < error["ts"]:
                conf = temporal_confidence(deploy["ts"], error["ts"], base=0.75)
                causal_chain.append({
                    "cause_event_id":  f"deploy:{deploy.get('version','unknown')}",
                    "effect_event_id": f"error:{error.get('msg','')[:40]}",
                    "evidence":  f"Deploy {deploy.get('version')} preceded error",
                    "confidence": conf
                })
    for spike in spikes:
        if spike["ts"] <= signal["ts"]:
            conf = temporal_confidence(spike["ts"], signal["ts"], base=0.90)
            causal_chain.append({
                "cause_event_id":  f"spike:{spike.get('name','metric')}",
                "effect_event_id": f"incident:{incident_id}",
                "evidence":  "Metric spike preceded incident declaration",
                "confidence": conf
            })

    # Step 4: fingerprint + motif
    fp_current    = extract_fingerprint(related_events, trigger, signal["ts"])
    motif_current = extract_motif(causal_chain)
    memory.update_incident_record(incident_id, fp_current, causal_chain)

    # Step 5: similar past incidents (key: "incident_id")
    past_incidents = memory.get_all_past_incidents(exclude_id=incident_id)
    scored = []
    for past in past_incidents:
        fp_past    = past["fingerprint"]
        motif_past = extract_motif(past["causal_chain"])
        sim = combined_similarity(fp_current, fp_past, motif_current, motif_past)
        if sim >= 0.35:
            scored.append({
                "incident_id": past["incident_id"],   # ← correct key
                "similarity":  sim,
                "rationale": (
                    f"Matched via fingerprint+motif ({get_motif_name(motif_current)}). "
                    f"trigger_type={'match' if fp_current.get('trigger_type')==fp_past.get('trigger_type') else 'diff'}, "
                    f"had_deploy={fp_current.get('had_deploy')}/{fp_past.get('had_deploy')}, "
                    f"had_spike={fp_current.get('had_spike')}/{fp_past.get('had_spike')}"
                )
            })
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    similar_past_incidents = scored[:5]

    # Step 6: remediations
    suggested_remediations = []
    seen_actions = set()
    for match in similar_past_incidents[:3]:
        rems = memory.get_remediations_for_incident(match["incident_id"])
        for action, target_id, version, outcome in rems:
            action_key = (action, target_id)
            if action_key in seen_actions:
                continue
            seen_actions.add(action_key)
            current_target = memory.tig.current_name(target_id)
            conf = round(match["similarity"] * (1.0 if outcome == "resolved" else 0.4), 3)
            suggested_remediations.append({
                "action":             action,
                "target":             current_target,
                "historical_outcome": outcome,
                "confidence":         conf
            })

    # Step 7: overall confidence
    confidence = 0.2
    if related_events:             confidence += 0.2
    if causal_chain:               confidence += 0.2
    if similar_past_incidents:     confidence += 0.2
    if suggested_remediations:     confidence += 0.2
    confidence = round(min(confidence, 1.0), 3)

    # Step 8: explain
    n_events   = len(related_events)
    motif_name = get_motif_name(motif_current)
    top_match  = similar_past_incidents[0] if similar_past_incidents else None   # FIX B9
    top_rem    = suggested_remediations[0] if suggested_remediations else None

    parts = [f"Incident context reconstructed from {n_events} related events."]
    if causal_chain:
        best_edge = max(causal_chain, key=lambda e: e["confidence"])
        parts.append(
            f"Causal chain: {best_edge['evidence']} "
            f"(confidence {best_edge['confidence']:.2f}). "
            f"Structural motif: {motif_name}."
        )
    if top_match:
        parts.append(
            f"Most similar past incident: {top_match['incident_id']} "
            f"(similarity {top_match['similarity']:.2f})."
        )
    if top_rem:
        parts.append(
            f"Suggested fix: {top_rem['action']} {top_rem['target']} "
            f"(historical outcome: {top_rem['historical_outcome']}, "
            f"confidence {top_rem['confidence']:.2f})."
        )
    if mode == "deep":
        parts.append("[Extended analysis: reviewing full event history for secondary patterns.]")

    return {
        "related_events":         related_events,
        "causal_chain":           causal_chain,
        "similar_past_incidents": similar_past_incidents,
        "suggested_remediations": suggested_remediations,
        "confidence":             confidence,
        "explain":                " ".join(parts),
    }
```

---

### 8.6 Adapter (adapters/myteam.py)

```python
from adapter import Adapter
from schema import Event, IncidentSignal, Context
from engine.memory import Memory
from engine.detective import reconstruct

class Engine(Adapter):
    def __init__(self):
        self.memory = Memory()

    def ingest(self, events):
        self.memory.store_events_batch(list(events))

    def reconstruct_context(self, signal, mode="fast"):
        return reconstruct(self.memory, signal, mode=mode)

    def close(self):
        self.memory.db.close()
```

---

## 9. Testing Strategy

### Test 0: Temporal Identity Graph — Rename (Gate Test)

```python
from engine.temporal_identity_graph import TemporalIdentityGraph

g = TemporalIdentityGraph()
id_before = g.lookup("payments-svc")
g.rename("payments-svc", "billing-svc", ts="2026-05-10T14:30:00Z")
id_after = g.lookup("billing-svc")

assert id_before != id_after,              "rename must create new node"
assert id_before in g.ancestors(id_after), "ancestors must trace back"
assert g.current_name(id_before) == "billing-svc", "current_name must follow forward"
print("PASS: rename")
```

Do not proceed until this passes.

### Test 0b: dep_add / dep_remove no-op

```python
events = [
    {"ts": "2026-05-10T14:00:00Z", "kind": "topology",
     "change": "dep_add", "from_": "checkout-api", "to": "payments-svc"},
    {"ts": "2026-05-10T14:00:01Z", "kind": "topology",
     "change": "dep_remove", "from_": "checkout-api", "to": "payments-svc"},
]
from engine.memory import Memory
m = Memory()
m.store_events_batch(events)   # must not raise
# verify events table is empty (topology events not stored)
rows = m.db.execute("SELECT COUNT(*) FROM events").fetchone()
assert rows[0] == 0, "topology events must not appear in events table"
print("PASS: dep_add/dep_remove no-op")
```

### Test 0c: field names in output

```python
# Verify all three critical field name fixes are present
ctx = reconstruct(memory, signal, mode="fast")

# B1 fix: incident_id not past_incident_id
for match in ctx["similar_past_incidents"]:
    assert "incident_id" in match, "must use incident_id key"
    assert "past_incident_id" not in match, "past_incident_id is wrong key"

# B2 fix: cause_event_id / effect_event_id
for edge in ctx["causal_chain"]:
    assert "cause_event_id" in edge, "must use cause_event_id"
    assert "effect_event_id" in edge, "must use effect_event_id"
    assert "cause_id" not in edge, "cause_id is wrong key"
    assert "effect_id" not in edge, "effect_id is wrong key"

print("PASS: field names")
```

### Test 1: Topology — Split and Merge

```python
# Split
g = TemporalIdentityGraph()
id_orig = g.lookup("payments-svc")
g.split("payments-svc", ["billing-svc", "refunds-svc"], ts="2026-05-10T15:00:00Z")
id_billing = g.lookup("billing-svc")
id_refunds  = g.lookup("refunds-svc")
assert id_orig in g.ancestors(id_billing)
assert id_orig in g.ancestors(id_refunds)
assert id_billing != id_refunds
print("PASS: split")

# Merge
g2 = TemporalIdentityGraph()
id_a = g2.lookup("payments-svc")
id_b = g2.lookup("billing-svc")
g2.merge(["payments-svc", "billing-svc"], "finance-svc", ts="2026-05-10T16:00:00Z")
id_fin = g2.lookup("finance-svc")
assert id_a in g2.ancestors(id_fin)
assert id_b in g2.ancestors(id_fin)
print("PASS: merge")
```

### Test 2: Memory — All Event Types

Ingest one of each kind. Verify:

- events table has rows for deploy, log, metric, trace, incident_signal
- topology events are NOT in events table
- incidents table has one row with trigger_type = "alert", trigger_metric = "latency_p99_ms"
- dep_add / dep_remove events do not crash and do not appear in events table

### Test 3: Motif Extraction and Similarity

```python
from engine.motif import extract_motif, motif_similarity

chain_full = [
    {"cause_event_id": "deploy:v2.14.0",       "effect_event_id": "spike:latency", ...},
    {"cause_event_id": "spike:latency",          "effect_event_id": "error:timeout", ...},
    {"cause_event_id": "error:timeout",          "effect_event_id": "incident:INC-1", ...},
]
chain_short = [
    {"cause_event_id": "deploy:v2.14.0",       "effect_event_id": "spike:latency", ...},
    {"cause_event_id": "spike:latency",          "effect_event_id": "incident:INC-2", ...},
]
chain_unrelated = [
    {"cause_event_id": "metric_spike:cpu",     "effect_event_id": "incident:INC-3", ...},
]

m_full  = extract_motif(chain_full)
m_short = extract_motif(chain_short)
m_unrel = extract_motif(chain_unrelated)

assert motif_similarity(m_full, m_short)  > 0.5
assert motif_similarity(m_full, m_unrel)  < 0.4
assert motif_similarity(m_full, ())       == 0.0
print("PASS: motif")
```

Note: `extract_motif` must now read `cause_event_id` and `effect_event_id` from
causal chain dicts. Update `edge_kind()` to use the correct field names:

```python
def extract_motif(causal_chain: list[dict]) -> tuple:
    if not causal_chain:
        return ()

    def edge_kind(edge_id: str) -> str:
        prefix = edge_id.split(":")[0].lower()
        mapping = {
            "deploy":   "deploy",
            "spike":    "metric_spike",
            "metric":   "metric_spike",
            "error":    "log_error",
            "log":      "log_error",
            "incident": "incident",
        }
        return mapping.get(prefix, "unknown")

    nodes_in_order = []
    seen = set()
    for edge in causal_chain:
        # FIX: read cause_event_id / effect_event_id not cause_id / effect_id
        cause_kind  = edge_kind(edge["cause_event_id"])
        effect_kind = edge_kind(edge["effect_event_id"])
        if cause_kind not in seen:
            nodes_in_order.append(cause_kind)
            seen.add(cause_kind)
        if effect_kind not in seen:
            nodes_in_order.append(effect_kind)
            seen.add(effect_kind)

    if not nodes_in_order:
        return ()

    result = []
    for i, kind in enumerate(nodes_in_order):
        if i == 0:
            role = "cause"
        elif i == len(nodes_in_order) - 1:
            role = "effect"
        else:
            role = "intermediate"
        result.append((kind, role))
    return tuple(result)
```

### Test 4: Worked Example End-to-End

Use the exact event sequence from the problem statement. After ingest and
reconstruct_context on INC-714:

- related_events includes deploy, log, metric, trace
- causal_chain has at least one edge with `cause_event_id` and `confidence > 0.5`
- `similar_past_incidents` is empty (no training data yet — correct)
- confidence > 0.4
- explain is a non-empty string

### Test 5: Cross-Rename Recall (The Primary Benchmark Test)

```
Ingest:
  Deploy v2.13.0 under payments-svc → spike → errors → INC-001-0 → rollback (resolved)

Then:
  topology rename: payments-svc → billing-svc  (event uses "from_" field)

Then:
  Deploy v2.15.0 under billing-svc → spike → errors → INC-002-0

Call reconstruct_context on INC-002-0 signal with service="billing-svc".
Assert:
  "INC-001-0" appears in similar_past_incidents
  similar_past_incidents[0]["incident_id"] == "INC-001-0"   # correct key
  similar_past_incidents[0]["similarity"] > 0.35
  suggested_remediations[0]["action"] == "rollback"
  suggested_remediations[0]["target"] == "billing-svc"      # current name
```

### Test 6: dep_add does not corrupt identity

```
Ingest:
  dep_add topology event: from_=checkout-api, to=payments-svc

Verify:
  g.lookup("checkout-api") works correctly
  g.lookup("payments-svc") works correctly
  Neither service is corrupted by the dep_add event
```

---

## 10. Build Order

### Hours 0–1: Environment

- Create folder structure, empty files
- `pip install duckdb`
- Verify Python 3.9+

### Hours 1–3: TemporalIdentityGraph

- Write engine/temporal_identity_graph.py
- Write tests/test_rename.py, test_split_merge.py
- Run all identity tests. Fix until PASS.
- **Gate: do not proceed until all identity tests pass.**

### Hours 3–6: Memory

- Write engine/memory.py
- **Use `event["from_"]` for all topology field access**
- **Handle dep_add / dep_remove as no-ops**
- Write tests/test_ingest.py
- Run test_ingest + test 0b (dep_add no-op). Fix until PASS.

### Hours 6–8: Fingerprint

- Write engine/fingerprint.py (unchanged from v2 spec)
- Test parse_trigger on `"alert:{svc}/latency_p99_ms>3000"` — should give `("alert", "latency_p99_ms")`
- Test fingerprint_similarity on matching and non-matching pairs

### Hours 8–10: Motif

- Write engine/motif.py
- **extract_motif reads `cause_event_id` / `effect_event_id`**
- Write tests/test_motif.py
- Run test_motif. Fix until PASS.

### Hours 10–14: Detective

- Write engine/detective.py
- **Use `signal.get("service")` as primary**
- **`incident_id` key in similar_past_incidents**
- **`cause_event_id` / `effect_event_id` in causal_chain**
- Run test 0c (field names). This must pass.
- Run test 4 (worked example)
- Run test 5 (cross-rename recall)

### Hours 14–15: Adapter

- Write adapters/myteam.py (~15 lines)
- Run main.py, verify full output shape matches schema.py

### Hours 15–18: Benchmark Integration

- Clone the repo
- Read schema.py — line-by-line verify field names in your output match
- Run `python self_check.py --adapter adapters.myteam:Engine --quick`
- Check the three field-name fixes are in place before interpreting scores

### Hours 18–21: Iteration

- Run full seeds: `python run.py --adapter adapters.myteam:Engine --seeds 9999 31415 27182`
- If recall@5 < 0.5: ancestors() fan-out is broken OR incident_id key is wrong
- If remediation_acc < 0.5: action is not "rollback" OR target_id → current_name broken
- If latency_p95_ms > 2000: fingerprint_json / causal_json columns are being recomputed instead of read

### Hours 21–23: Writeup + Demo

- Write 3-page PDF (see §12)
- Record 5-minute demo (see §13)

### Hours 23–24: Submission

---

## 11. Scoring — What to Maximise and How

### 11.1 recall@5 (0.30 weight) — the highest-value axis

**What hurts:**

- Wrong key name (`past_incident_id` instead of `incident_id`) → 0 recall
- Missing ancestors() fan-out → cross-rename recall = 0
- Threshold too high → misses valid matches

**Fix:** B1+B6 (key name), B3 (from\_ field), ancestors() in get_events_in_window.
The family integer is encoded in the incident_id suffix `-{fam}`. As long as you
store and return the original incident IDs, the scorer extracts the family correctly.

### 11.2 remediation_acc (0.20 weight)

**What the scorer checks:** `action == "rollback"`. That's it.

All benchmark incidents use rollback as the remediation. Your suggested_remediations
must have at least one entry with `action: "rollback"`. If you find a past incident
with a rollback remediation and the similarity ≥ 0.35, you get this point.

### 11.3 precision@5_mean (0.15 weight)

Fraction of top-5 matches that belong to the same family as the current incident.
Good fingerprint + motif similarity keeps non-family incidents below threshold.

### 11.4 latency_p95_ms (0.15 weight)

Scored as `min(1.0, 2000ms / p95_ms)`. At p95 = 500ms: 1.0. At p95 = 2000ms: 1.0.
At p95 = 4000ms: 0.5. DuckDB in-memory with JSON column reads is sub-millisecond
at L2 scale. You should hit near 1.0 with no tuning.

### 11.5 Manual axes (0.10 + 0.10 weight)

Panel grades `related_events` quality and the `explain` narrative. Both improve
automatically as the rest of the engine works correctly.

---

## 12. Writeup (3-Page PDF) — Unchanged from v2

**Page 1:** Memory representation and temporal identity graph
**Page 2:** Pattern matching — fingerprint + structural motif
**Page 3:** Evolution, known limitations, future work

---

## 13. Demo (5-Minute Script) — Unchanged from v2

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
duckdb>=0.10.0
```

---

## 15. README — Unchanged from v2

---

## 16. What NOT to Build — Unchanged from v2

---

## 17. Risk Register — Updated

| Risk                                             | Probability | Impact                                              | Mitigation                                                                  |
| ------------------------------------------------ | ----------- | --------------------------------------------------- | --------------------------------------------------------------------------- |
| Schema field name mismatch (B1/B2)               | High        | Kills 45% of automated score                        | Run test 0c before self_check. Check schema.py line-by-line.                |
| Topology `from_` field mismatch (B3)             | High        | All renames fail silently                           | Use `event["from_"]` everywhere. Grep codebase for `event["from"]` and fix. |
| `dep_add`/`dep_remove` crash (B5)                | Medium      | Engine crashes on 40% of topology events            | No-op handler, always present, with `else: pass` fallback                   |
| Global state leaks between seeds                 | Medium      | Inflated local score, fails multi-seed              | All state inside `__init__`, no module globals                              |
| Similarity threshold miscalibrated               | Medium      | Low precision or recall                             | Start at 0.35; adjust after self_check                                      |
| ancestors() walk missing in get_events_in_window | Medium      | Cross-rename recall = 0                             | Always union `tig.ancestors(canonical_id)` in the query                     |
| Motif reads wrong field names from causal chain  | Medium      | Motif = () for all incidents → no structural signal | extract_motif reads `cause_event_id`/`effect_event_id`                      |
| Out-of-order events corrupt fingerprint          | Low         | Wrong deploy_gap_s                                  | Batch pre-sorted by ts before processing                                    |
| DuckDB version incompatibility                   | Low         | Import errors                                       | `duckdb>=0.10.0` (any recent version works)                                 |

---

## 18. Quick Audit Checklist

Run this grep before self_check. Every hit is a bug.

```bash
# Must be ZERO hits — these are all wrong field names
grep -r '"from"'         engine/ adapters/  # should be "from_"
grep -r 'cause_id'       engine/ adapters/  # should be cause_event_id
grep -r 'effect_id'      engine/ adapters/  # should be effect_event_id
grep -r 'past_incident'  engine/ adapters/  # should be incident_id
grep -r 'event\["from"\]' engine/           # wrong topology field

# Must be NONZERO hits — these are correct patterns
grep -r 'from_'           engine/            # topology field
grep -r 'cause_event_id'  engine/            # causal edge field
grep -r 'effect_event_id' engine/            # causal edge field
grep -r '"incident_id"'   engine/            # match field
grep -r 'signal.get.*service' engine/        # primary service lookup
grep -r 'dep_add\|dep_remove' engine/        # no-op handler
```

---

_Fix B1 (incident*id key), B2 (cause_event_id/effect_event_id), and B3 (from* field) in that order.
These three bugs alone account for the majority of possible automated score loss.
Everything else is tuning._
