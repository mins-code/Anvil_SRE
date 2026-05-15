import sys
import os
import json

# Force Python to recognize the root project directory
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from adapters.myteam import Engine

def run_worked_example():
    print("1. Initializing Engine...")
    engine = Engine()

    # The exact JSONL sample from the problem statement
    sample_data = [
        {"ts": "2026-05-10T14:21:30Z", "kind": "deploy",  "service": "payments-svc", "version": "v2.14.0", "actor": "ci"},
        {"ts": "2026-05-10T14:22:01Z", "kind": "log",     "service": "checkout-api", "level": "error", "msg": "timeout calling payments-svc", "trace_id": "abc123"},
        {"ts": "2026-05-10T14:22:01Z", "kind": "metric",  "service": "payments-svc", "name": "latency_p99_ms", "value": 4820},
        {"ts": "2026-05-10T14:22:08Z", "kind": "trace",   "trace_id": "abc123", "spans": [{"svc": "checkout-api", "dur_ms": 5012}, {"svc": "payments-svc", "dur_ms": 4980}]},
        {"ts": "2026-05-10T14:30:00Z", "kind": "topology", "change": "rename", "from_": "payments-svc", "to": "billing-svc"}, 
        {"ts": "2026-05-10T14:32:11Z", "kind": "incident_signal", "incident_id": "INC-714", "trigger": "alert:checkout-api/error-rate>5%"},
        {"ts": "2026-05-10T15:10:00Z", "kind": "remediation", "incident_id": "INC-714", "action": "rollback", "target": "billing-svc", "version": "v2.13.4", "outcome": "resolved"}
    ]

    print("2. Ingesting Telemetry Data...")
    # Feed all the data into the engine
    engine.ingest(sample_data)

    print("3. Triggering Reconstruct Context...")
    # Extract just the incident signal to pass into the reconstruct function
    incident_signal = sample_data[5] 
    
    # Run the reconstruction!
    context = engine.reconstruct_context(incident_signal, mode="fast")

    print("\n--- ENGINE OUTPUT ---")
    # Print the resulting dictionary beautifully formatted
    print(json.dumps(context, indent=2))
    print("---------------------\n")

    print("4. Validating Output Shape...")
    # These "assert" statements check if the required keys exist in your output.
    # If a key is missing, Python will throw an AssertionError and crash.
    assert "related_events" in context, "Missing 'related_events' key!"
    assert "causal_chain" in context, "Missing 'causal_chain' key!"
    assert "similar_past_incidents" in context, "Missing 'similar_past_incidents' key!"
    assert "suggested_remediations" in context, "Missing 'suggested_remediations' key!"
    assert "explain" in context, "Missing 'explain' key!"

    print("✅ SUCCESS! The engine output matches the required Context shape.")
    
    # Clean up the database
    engine.close()

if __name__ == "__main__":
    run_worked_example()