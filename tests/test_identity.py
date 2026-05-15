from engine.temporal_identity_graph import TemporalIdentityGraph

def test_rename():
    """Test 0 (Gate Test): Verify a basic service identity persists."""
    g = TemporalIdentityGraph()
    id_before = g.lookup("payments-svc", at_time="2026-05-10T14:00:00Z")
    
    g.rename(from_="payments-svc", to="billing-svc", ts="2026-05-10T14:30:00Z")
    
    id_after = g.lookup("billing-svc", at_time="2026-05-10T15:00:00Z")
    
    assert id_before != id_after, "rename must create new node"
    assert id_before in g.ancestors(id_after), "ancestors must trace back"
    assert g.current_name(id_before) == "billing-svc", "current_name must follow forward"

def test_split_merge():
    """Test 1 (Split/Merge): Verify that when Service A splits into B and C, B and C both reference A as an ancestor."""
    g = TemporalIdentityGraph()
    
    # Split
    id_a = g.lookup("service-a", at_time="2026-01-01T10:00:00Z")
    g.split(from_="service-a", into=["service-b", "service-c"], ts="2026-02-01T10:00:00Z")
    
    id_b = g.lookup("service-b", at_time="2026-03-01T10:00:00Z")
    id_c = g.lookup("service-c", at_time="2026-03-01T10:00:00Z")
    
    assert id_a in g.ancestors(id_b)
    assert id_a in g.ancestors(id_c)
    
    # Merge
    g.merge(from_=["service-b", "service-c"], into="service-d", ts="2026-04-01T10:00:00Z")
    id_d = g.lookup("service-d", at_time="2026-05-01T10:00:00Z")
    
    assert id_b in g.ancestors(id_d)
    assert id_c in g.ancestors(id_d)
    assert id_a in g.ancestors(id_d) # transitive ancestor

if __name__ == "__main__":
    test_rename()
    test_split_merge()
    print("All tests passed.")
