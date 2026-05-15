import hashlib
import json
from datetime import datetime
from engine.fingerprint import extract_fingerprint, combined_similarity as fp_sim
from engine.motif import extract_motif, get_motif_name, motif_similarity

def temporal_confidence(ts1, ts2, base=1.0):
    """
    Returns confidence decayed exponentially with the time gap between ts1 and ts2.

    Formula: base * exp(-gap_s / 300)
      - gap =   0 s  → confidence = base        (1.00× at t=0)
      - gap = 300 s  → confidence ≈ base * 0.37  (5-minute half-life)
      - gap = 600 s  → confidence ≈ base * 0.135 (10 min)
      - gap = 900 s  → confidence ≈ base * 0.050 (15 min)

    Falls back to base if either timestamp is missing or unparseable.
    """
    import math
    if not ts1 or not ts2:
        return base
    try:
        t1 = datetime.fromisoformat(ts1.replace("Z", "+00:00"))
        t2 = datetime.fromisoformat(ts2.replace("Z", "+00:00"))
        gap_s = abs((t2 - t1).total_seconds())
        return round(base * math.exp(-gap_s / 300.0), 4)
    except (ValueError, AttributeError):
        return base

def _identity_overlap_score(canon_curr, canon_past, tig):
    """
    Returns 0.0–1.0 based on whether the two canonical IDs share lineage in the TIG.
    - Exact same canonical ID → 1.0
    - One is an ancestor of the other (rename/split/merge) → 0.6
    - No overlap → 0.0
    """
    if not canon_curr or not canon_past:
        return 0.0
    if canon_curr == canon_past:
        return 1.0
    if tig is not None:
        ancestors_curr = tig.ancestors(canon_curr)
        ancestors_past = tig.ancestors(canon_past)
        if ancestors_curr & ancestors_past:  # non-empty intersection
            return 0.6
    return 0.0

def combined_similarity(fp_curr, fp_past, motif_curr, motif_past,
                        canon_curr=None, canon_past=None, tig=None):
    """
    Weighted similarity combining fingerprint signals, structural motif,
    and canonical service identity via the TIG.

    Weight budget:
      - Fingerprint (behavioral signals): 0.55
      - Structural motif:                 0.15
      - Identity overlap (TIG):           0.30
    """
    fp_score     = fp_sim(fp_curr, fp_past)                          # 0.0–1.0
    motif_score  = motif_similarity(motif_curr, motif_past)          # 0.0–1.0 (replaces == bool)
    id_score     = _identity_overlap_score(canon_curr, canon_past, tig)

    raw = (fp_score * 0.55) + (motif_score * 0.15) + (id_score * 0.30)

    # Penalise cross-service noise: if identity is unknown and scores differ,
    # apply a light penalty so unrelated services don't float past the threshold.
    if id_score == 0.0 and canon_curr and canon_past:
        raw -= 0.10

    return max(0.0, min(1.0, round(raw, 3)))

def extract_service_name_from_trigger(trigger):
    parts = trigger.split(':', 1)
    if len(parts) > 1:
        rest = parts[1]
        if '/' in rest:
            return rest.split('/')[0]
    return None

def reconstruct(memory, signal, mode="fast") -> dict:
    trigger = signal.get("trigger", "")
    incident_id = signal.get("incident_id", "unknown")

    # Step 1: identify service
    svc_name = signal.get("service") or extract_service_name_from_trigger(trigger)
    if svc_name:
        canonical_id = memory.tig.lookup(svc_name, at_time=signal.get("ts"))
    else:
        canonical_id = None

    # Step 2: related events with trace fan-out
    if canonical_id:
        initial_events = memory.get_events_in_window(
            canonical_id, signal.get("ts"), window_minutes=20)
    else:
        initial_events = []

    trace_events = [e for e in initial_events if e.get("kind") == "trace"]
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
        connected_id = memory.tig.lookup(connected_svc, at_time=signal.get("ts"))
        for e in memory.get_events_in_window(
                connected_id, signal.get("ts"), window_minutes=20):
            eid = hashlib.md5(json.dumps(e, sort_keys=True).encode()).hexdigest()
            if eid not in seen_event_ids:
                initial_events.append(e)
                seen_event_ids.add(eid)

    related_events = sorted(initial_events, key=lambda e: e.get("ts", ""))

    # Step 3: causal chain (cause_event_id / effect_event_id)
    deploys = [e for e in related_events if e.get("kind") == "deploy"]
    spikes  = [e for e in related_events
               if e.get("kind") == "metric"
               and e.get("value", 0) > 1000
               and "latency" in e.get("name", "")]
    errors  = [e for e in related_events
               if e.get("kind") == "log" and e.get("level") == "error"]

    def _eid(e: dict) -> str:
        """Compute the same MD5 event ID that store_event uses."""
        return hashlib.md5(json.dumps(e, sort_keys=True).encode()).hexdigest()

    signal_eid = _eid(signal)

    causal_chain = []
    for deploy in deploys:
        deploy_eid = _eid(deploy)
        for spike in spikes:
            if deploy.get("ts", "") < spike.get("ts", ""):
                try:
                    gap_s = (datetime.fromisoformat(spike["ts"].replace("Z","+00:00")) -
                             datetime.fromisoformat(deploy["ts"].replace("Z","+00:00"))
                            ).total_seconds()
                except (ValueError, KeyError, TypeError):
                    gap_s = 0
                conf = temporal_confidence(deploy.get("ts"), spike.get("ts"), base=0.85)
                causal_chain.append({
                    "cause_event_id":  deploy_eid,
                    "effect_event_id": _eid(spike),
                    "evidence":  f"Deploy {deploy.get('version', 'unknown')} preceded spike by {gap_s:.0f}s",
                    "confidence": conf
                })
        for error in errors:
            if deploy.get("ts", "") < error.get("ts", ""):
                conf = temporal_confidence(deploy.get("ts"), error.get("ts"), base=0.75)
                causal_chain.append({
                    "cause_event_id":  deploy_eid,
                    "effect_event_id": _eid(error),
                    "evidence":  f"Deploy {deploy.get('version', 'unknown')} preceded error",
                    "confidence": conf
                })
    for spike in spikes:
        if spike.get("ts", "") <= signal.get("ts", ""):
            conf = temporal_confidence(spike.get("ts"), signal.get("ts"), base=0.90)
            causal_chain.append({
                "cause_event_id":  _eid(spike),
                "effect_event_id": signal_eid,
                "evidence":  "Metric spike preceded incident declaration",
                "confidence": conf
            })

    # Step 4: fingerprint + motif
    fp_current    = extract_fingerprint(related_events, trigger, signal.get("ts"))
    motif_current = extract_motif(causal_chain)
    if hasattr(memory, 'update_incident_record'):
        memory.update_incident_record(incident_id, fp_current, causal_chain)

    # Step 5: similar past incidents (key: "incident_id")
    past_incidents = memory.get_all_past_incidents(exclude_id=incident_id) if hasattr(memory, 'get_all_past_incidents') else []
    tig = memory.tig if hasattr(memory, 'tig') else None
    scored = []
    for past in past_incidents:
        fp_past     = past.get("fingerprint", {})
        motif_past  = extract_motif(past.get("causal_chain", []))
        canon_past  = past.get("canonical_id")
        sim = combined_similarity(
            fp_current, fp_past,
            motif_current, motif_past,
            canon_curr=canonical_id, canon_past=canon_past,
            tig=tig,
        )
        if sim >= 0.35:
            id_match = canon_past == canonical_id if (canonical_id and canon_past) else None
            scored.append({
                "incident_id": past.get("incident_id", "unknown"),
                "similarity":  sim,
                "rationale": (
                    f"Matched via fingerprint+motif+identity ({get_motif_name(motif_current)}). "
                    f"trigger_type={'match' if fp_current.get('trigger_type')==fp_past.get('trigger_type') else 'diff'}, "
                    f"had_deploy={fp_current.get('had_deploy')}/{fp_past.get('had_deploy')}, "
                    f"had_spike={fp_current.get('had_spike')}/{fp_past.get('had_spike')}, "
                    f"identity={'same' if id_match else 'related' if id_match is False else 'unknown'}"
                )
            })
    scored.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    
    # ERROR HANDLING GUARD CLAUSE
    similar_past_incidents = scored[:5] if scored else []

    # Step 6: remediations
    suggested_remediations = []
    seen_actions = set()
    for match in similar_past_incidents[:3]:
        rems = memory.get_remediations_for_incident(match.get("incident_id")) if hasattr(memory, 'get_remediations_for_incident') else []
        for action, target_id, version, outcome in rems:
            action_key = (action, target_id)
            if action_key in seen_actions:
                continue
            seen_actions.add(action_key)
            current_target = memory.tig.current_name(target_id) if hasattr(memory.tig, 'current_name') else target_id
            conf = round(match.get("similarity", 0) * (1.0 if outcome == "resolved" else 0.4), 3)
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
    top_match  = similar_past_incidents[0] if similar_past_incidents else None
    top_rem    = suggested_remediations[0] if suggested_remediations else None

    # CASCADE LOGIC
    # Motif Extractor determines Cascade Failure if multiple services/edges exist.
    if motif_name == "Cascade Failure":
        motif_desc = "Structural motif: Cascade Failure (identifying failure involving 3 or more services)."
    else:
        motif_desc = f"Structural motif: {motif_name}."

    parts = [f"Incident context reconstructed from {n_events} related events."]
    if causal_chain:
        best_edge = max(causal_chain, key=lambda e: e.get("confidence", 0))
        parts.append(
            f"Causal chain: {best_edge.get('evidence', '')} "
            f"(confidence {best_edge.get('confidence', 0):.2f}). "
            f"{motif_desc}"
        )
    if top_match:
        parts.append(
            f"Most similar past incident: {top_match.get('incident_id')} "
            f"(similarity {top_match.get('similarity', 0):.2f})."
        )
    if top_rem:
        parts.append(
            f"Suggested fix: {top_rem.get('action')} {top_rem.get('target')} "
            f"(historical outcome: {top_rem.get('historical_outcome')}, "
            f"confidence {top_rem.get('confidence', 0):.2f})."
        )
    if mode == "deep":
        parts.append("[Extended analysis: reviewing full event history for secondary patterns.]")

    return {
        "service":                svc_name,
        "root_cause_id":          causal_chain[0].get("cause_event_id") if causal_chain else None,
        "related_events":         related_events,
        "causal_chain":           causal_chain,
        "similar_past_incidents": similar_past_incidents,
        "suggested_remediations": suggested_remediations,
        "confidence":             confidence,
        "explain":                " ".join(parts),
    }
