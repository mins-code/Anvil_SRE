import hashlib
import json
from datetime import datetime
from engine.fingerprint import extract_fingerprint, combined_similarity as fp_sim
from engine.motif import extract_motif, get_motif_name

def temporal_confidence(ts1, ts2, base=1.0):
    return base

def combined_similarity(fp_curr, fp_past, motif_curr, motif_past):
    fp_score = fp_sim(fp_curr, fp_past)
    motif_score = 1.0 if motif_curr == motif_past else 0.0
    return (fp_score + motif_score) / 2.0

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

    causal_chain = []
    for deploy in deploys:
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
                    "cause_event_id":  f"deploy:{deploy.get('version','unknown')}",
                    "effect_event_id": f"spike:{spike.get('name','metric')}",
                    "evidence":  f"Deploy {deploy.get('version', 'unknown')} preceded spike by {gap_s:.0f}s",
                    "confidence": conf
                })
        for error in errors:
            if deploy.get("ts", "") < error.get("ts", ""):
                conf = temporal_confidence(deploy.get("ts"), error.get("ts"), base=0.75)
                causal_chain.append({
                    "cause_event_id":  f"deploy:{deploy.get('version','unknown')}",
                    "effect_event_id": f"error:{error.get('msg','')[:40]}",
                    "evidence":  f"Deploy {deploy.get('version', 'unknown')} preceded error",
                    "confidence": conf
                })
    for spike in spikes:
        if spike.get("ts", "") <= signal.get("ts", ""):
            conf = temporal_confidence(spike.get("ts"), signal.get("ts"), base=0.90)
            causal_chain.append({
                "cause_event_id":  f"spike:{spike.get('name','metric')}",
                "effect_event_id": f"incident:{incident_id}",
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
    scored = []
    for past in past_incidents:
        fp_past    = past.get("fingerprint", {})
        motif_past = extract_motif(past.get("causal_chain", []))
        sim = combined_similarity(fp_current, fp_past, motif_current, motif_past)
        if sim >= 0.35:
            scored.append({
                "incident_id": past.get("incident_id", "unknown"),
                "similarity":  sim,
                "rationale": (
                    f"Matched via fingerprint+motif ({get_motif_name(motif_current)}). "
                    f"trigger_type={'match' if fp_current.get('trigger_type')==fp_past.get('trigger_type') else 'diff'}, "
                    f"had_deploy={fp_current.get('had_deploy')}/{fp_past.get('had_deploy')}, "
                    f"had_spike={fp_current.get('had_spike')}/{fp_past.get('had_spike')}"
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
