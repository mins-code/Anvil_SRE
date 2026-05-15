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
    error_count = 0
    has_traces = False
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
                error_count += 1
                
        elif kind == "trace":
            has_traces = True

    deploy_gap_s = 0.0
    gap_bucket = "none"
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
                
            if deploy_gap_s < 60:
                gap_bucket = "instant"
            elif deploy_gap_s <= 300:
                gap_bucket = "rapid"
            elif deploy_gap_s <= 1800:
                gap_bucket = "delayed"
        except ValueError:
            pass

    return {
        "trigger_type": trigger_type,
        "trigger_metric": trigger_metric,
        "had_deploy": had_deploy,
        "had_spike": had_spike,
        "error_count": error_count,
        "has_traces": has_traces,
        "deploy_gap_s": deploy_gap_s,
        "gap_bucket": gap_bucket,
        "event_kinds": list(event_kinds)
    }

def combined_similarity(fp1, fp2):
    """
    Returns a float 0.0-1.0 indicating similarity between two fingerprints.
    """
    score = 0.0
    
    # Trigger type match
    if fp1.get("trigger_type") == fp2.get("trigger_type"):
        score += 0.20
    else:
        # Significant penalty for completely different trigger types
        score -= 0.20

    m1 = fp1.get("trigger_metric")
    m2 = fp2.get("trigger_metric")
    
    metrics_match = False
    if m1 and m2:
        if m1 == m2:
            score += 0.50  # Large bonus for exact metric match
            metrics_match = True
    elif not m1 and not m2:
        metrics_match = True

    # Both had deploys
    if fp1.get("had_deploy") == fp2.get("had_deploy"):
        score += 0.25

    # Both had spikes
    if fp1.get("had_spike") == fp2.get("had_spike"):
        score += 0.20

    # Both had errors logic replaced with Error Count Similarity
    ec1 = fp1.get("error_count", 0)
    ec2 = fp2.get("error_count", 0)
    if ec1 > 0 and ec2 > 0:
        ratio = min(ec1, ec2) / max(ec1, ec2)
        score += 0.15 * ratio
    elif ec1 == 0 and ec2 == 0:
        score += 0.15

    # Traces active
    if fp1.get("has_traces") == fp2.get("has_traces"):
        score += 0.05

    # Gap Bucket Match
    gb1 = fp1.get("gap_bucket", "none")
    gb2 = fp2.get("gap_bucket", "none")
    if gb1 == gb2 and gb1 != "none":
        score += 0.10
    elif gb1 == "none" and gb2 == "none":
        score += 0.05

    # Hard Multiplicative Penalty: If metrics are present but do not match, 
    # crush the score so it never outranks a correct metric match.
    if m1 and m2 and not metrics_match:
        score *= 0.1

    # Ensure bounds between 0.0 and 1.0
    return max(0.0, min(1.0, round(score, 3)))

