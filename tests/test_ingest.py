import pytest
from engine.memory import Memory

class MockTIG:
    def __init__(self):
        self.renames = []
        self.splits = []
        self.merges = []

    def rename(self, from_val, to_val):
        self.renames.append((from_val, to_val))

    def split(self, from_val, to_val):
        self.splits.append((from_val, to_val))
        
    def merge(self, from_val, to_val):
        self.merges.append((from_val, to_val))

@pytest.fixture
def memory():
    mem = Memory()
    # Inject Mock TIG
    mem.tig = MockTIG()
    return mem

def test_ingest_batch_ignores_dep_add(memory):
    """
    Test A: Ingest a batch of events containing one 'deploy', one 'metric', 
    and one 'topology' (change='dep_add').
    Verify that the 'events' table contains exactly 2 rows and topology was a no-op.
    """
    events = [
        {
            "kind": "deploy",
            "service": "api-service",
            "ts": "2026-05-10T10:00:00Z"
        },
        {
            # Edge case: missing 'service'
            "kind": "metric",
            "name": "latency",
            "value": 1500,
            "ts": "2026-05-10T10:01:00Z"
        },
        {
            "kind": "topology",
            "change": "dep_add",
            "from_": "api-service",
            "to": "db-service"
        }
    ]

    for event in events:
        memory.store_event(event)

    # Assert exactly 2 rows in events table
    result = memory.db.execute("SELECT kind FROM events").fetchall()
    assert len(result) == 2, f"Expected exactly 2 events to be stored, got {len(result)}"
    
    kinds = [r[0] for r in result]
    assert "deploy" in kinds
    assert "metric" in kinds
    assert "topology" not in kinds

def test_ingest_rename_topology(memory):
    """
    Test B: Ingest a 'rename' topology event using the from_ field and verify 
    that the TIG reflects the name change without crashing.
    """
    event = {
        "kind": "topology",
        "change": "rename",
        "from_": "old-service",
        "to": "new-service"
        # Edge case: missing 'ts'
    }

    memory.store_event(event)

    # Verify that TIG was called with the correct parameters
    assert len(memory.tig.renames) == 1
    assert memory.tig.renames[0] == ("old-service", "new-service")

    # Verify no row was inserted into the events table for topology
    result = memory.db.execute("SELECT * FROM events").fetchall()
    assert len(result) == 0, "Topology event should not be stored in events table"

def test_missing_fields_graceful_handling(memory):
    """
    Verify edge cases where critical fields might be missing in the input dictionaries
    to ensure it does not crash the ingest.
    """
    events = [
        {},                                         # Missing everything
        {"kind": "topology"},                       # Missing change
        {"kind": "topology", "change": "rename"},   # Missing from_ and to
        {"kind": "deploy"},                         # Missing ts, service
        {"kind": "trace", "spans": []}              # Missing spans data
    ]
    
    for event in events:
        try:
            memory.store_event(event)
        except Exception as e:
            pytest.fail(f"store_event crashed on edge case {event}: {e}")
