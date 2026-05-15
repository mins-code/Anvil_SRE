from engine.motif import extract_motif, motif_similarity, get_motif_name

chain_full = [
    {"cause_event_id": "deploy:v2.14.0",   "effect_event_id": "spike:latency",   "confidence": 0.8},
    {"cause_event_id": "spike:latency",     "effect_event_id": "error:timeout",   "confidence": 0.7},
    {"cause_event_id": "error:timeout",     "effect_event_id": "incident:INC-1",  "confidence": 0.9},
]
chain_short = [
    {"cause_event_id": "deploy:v2.14.0",   "effect_event_id": "spike:latency",   "confidence": 0.8},
    {"cause_event_id": "spike:latency",     "effect_event_id": "incident:INC-2",  "confidence": 0.9},
]
chain_unrelated = [
    {"cause_event_id": "metric_spike:cpu", "effect_event_id": "incident:INC-3",  "confidence": 0.6},
]

m_full  = extract_motif(chain_full)
m_short = extract_motif(chain_short)
m_unrel = extract_motif(chain_unrelated)

print("m_full :", m_full)
print("m_short:", m_short)
print("m_unrel:", m_unrel)
print()

sim_fs = motif_similarity(m_full, m_short)
sim_fu = motif_similarity(m_full, m_unrel)
sim_fe = motif_similarity(m_full, ())

r1 = "PASS" if sim_fs > 0.5 else "FAIL"
r2 = "PASS" if sim_fu < 0.4 else "FAIL"
r3 = "PASS" if sim_fe == 0.0 else "FAIL"

print("similarity(full, short)     =", sim_fs, " (need > 0.5) ->", r1)
print("similarity(full, unrelated) =", sim_fu, " (need < 0.4) ->", r2)
print("similarity(full, ())        =", sim_fe, " (need == 0.0) ->", r3)
print()
print("get_motif_name(m_full) :", get_motif_name(m_full))
print("get_motif_name(m_short):", get_motif_name(m_short))
print("get_motif_name(())     :", get_motif_name(()))
