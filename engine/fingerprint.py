from datetime import datetime
import re

def extract_fingerprint(events, trigger, end_ts):
    """
    Extracts a behavioral fingerprint from a window of telemetry events.
    """
    # Parse trigger string, e.g., 'alert:service-name/latency_p99_ms>3000'
    parts = trigger.split(':', 1)
    trigger_type = parts[0] if len(parts) > 0 else "unknown"
    
    trigger_metric = ""
    if len(parts) > 1:
        rest = parts[1]
        if '/' in rest:
            metric_part = rest.split('/', 1)[1]
            trigger_metric = re.split(r'[><=]', metric_part)[0]

    had_deploy = False
    had_spike = False
    had_errors = False
    last_deploy_ts_str = None
    event_kinds = set()

    for e in events:
        kind = e.get("kind")
        if not kind:
            continue
            
        event_kinds.add(kind)
        
        if kind == "deploy":
            had_deploy = True
            ts_str = e.get("ts") or e.get("happened_at")
            if ts_str:
                if not last_deploy_ts_str or ts_str > last_deploy_ts_str:
                    last_deploy_ts_str = ts_str
                    
        elif kind == "metric":
            if e.get("value", 0) > 1000 and "latency" in e.get("name", ""):
                had_spike = True
                
        elif kind == "log":
            if e.get("level") == "error":
                had_errors = True

    deploy_gap_s = 0.0
    if had_deploy and last_deploy_ts_str and end_ts:
        try:
            # Handle ISO formats correctly, replacing Z with +00:00 for python < 3.11 compatibility
            fmt_deploy = last_deploy_ts_str.replace("Z", "+00:00")
            fmt_end = end_ts.replace("Z", "+00:00")
            
            t_deploy = datetime.fromisoformat(fmt_deploy)
            t_end = datetime.fromisoformat(fmt_end)
            deploy_gap_s = (t_end - t_deploy).total_seconds()
            
            # Ensure deploy gap is non-negative
            if deploy_gap_s < 0:
                deploy_gap_s = 0.0
        except ValueError:
            pass

    return {
        "trigger_type": trigger_type,
        "trigger_metric": trigger_metric,
        "had_deploy": had_deploy,
        "had_spike": had_spike,
        "had_errors": had_errors,
        "deploy_gap_s": deploy_gap_s,
        "event_kinds": list(event_kinds)
    }

def combined_similarity(fp1, fp2):
    """
    Returns a float 0.0-1.0 indicating similarity between two fingerprints.
    """
    score = 0.0
    
    # Trigger type and metric match (High Weight)
    if fp1.get("trigger_type") == fp2.get("trigger_type"):
        score += 0.30
        if fp1.get("trigger_metric") and fp1.get("trigger_metric") == fp2.get("trigger_metric"):
            score += 0.10  # Bonus for exact metric match
    else:
        # Significant penalty for completely different trigger types
        score -= 0.20

    # Both had deploys
    if fp1.get("had_deploy") == fp2.get("had_deploy"):
        score += 0.25

    # Both had spikes
    if fp1.get("had_spike") == fp2.get("had_spike"):
        score += 0.20

    # Both had errors
    if fp1.get("had_errors") == fp2.get("had_errors"):
        score += 0.15

    # Similar deploy gap
    gap1 = fp1.get("deploy_gap_s", 0)
    gap2 = fp2.get("deploy_gap_s", 0)
    if gap1 > 0 and gap2 > 0:
        ratio = min(gap1, gap2) / max(gap1, gap2)
        score += 0.10 * ratio
    elif gap1 == 0 and gap2 == 0:
        score += 0.10  # Both zero gap

    # Ensure bounds between 0.0 and 1.0
    return max(0.0, min(1.0, round(score, 3)))

