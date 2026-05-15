import duckdb

class Memory:
    def __init__(self):
        # Connect to an in-memory DuckDB database
        self.db = duckdb.connect(":memory:")
        self._initialize_tables()

    def _initialize_tables(self):
        # Every event that has ever been ingested
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id     TEXT PRIMARY KEY,
                happened_at  TEXT,
                kind         TEXT,
                canonical_id TEXT,
                service_name TEXT,
                raw_json     TEXT
            );
        """)

        # Every incident signal received
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id   TEXT PRIMARY KEY,
                happened_at   TEXT,
                canonical_id  TEXT,
                trigger       TEXT,
                had_deploy    INTEGER DEFAULT 0,
                had_spike     INTEGER DEFAULT 0,
                had_errors    INTEGER DEFAULT 0,
                deploy_gap_s  REAL    DEFAULT 0,
                error_types   TEXT    DEFAULT ''
            );
        """)

        # Every remediation event
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS remediations (
                incident_id   TEXT,
                action        TEXT,
                target_id     TEXT,
                version       TEXT,
                outcome       TEXT,
                happened_at   TEXT
            );
        """)

    def store_event(self, event: dict):
        import json
        import hashlib

        kind = event.get('kind')

        if kind == 'topology':
            change = event.get('change')
            
            # Python 'from' is a reserved keyword, so we extract 'from_'
            from_val = event.get('from_')
            to_val = event.get('to')
            into_val = event.get('into', [])

            if change in ('rename', 'split', 'merge'):
                # Route to TIG (TemporalIdentityGraph) instance
                if hasattr(self, 'tig') and self.tig is not None:
                    if change == 'rename':
                        self.tig.rename(from_val, to_val)
                    elif change == 'split':
                        self.tig.split(from_val, into_val)
                    elif change == 'merge':
                        self.tig.merge(from_val, to_val)
            elif change in ('dep_add', 'dep_remove'):
                # Graceful Handling: Must be a no-op
                pass
            else:
                pass # Other topology events
        else:
            # For deploy, log, metric, trace, etc.
            raw_json = json.dumps(event, sort_keys=True)
            event_id = hashlib.md5(raw_json.encode('utf-8')).hexdigest()
            happened_at = event.get('ts') or event.get('happened_at')
            
            if not happened_at:
                return  # Skip event if timestamp is missing
            
            # Extract service_name depending on event kind
            service_name = None
            if kind in ('deploy', 'log', 'metric'):
                service_name = event.get('service')
            elif kind == 'trace':
                spans = event.get('spans', [])
                if spans:
                    service_name = spans[0].get('svc')
            
            # Resolve to canonical_id (assuming TIG or NameBook has a lookup method)
            canonical_id = service_name
            if hasattr(self, 'tig') and self.tig is not None and service_name:
                # Placeholder for TIG lookup
                pass

            self.db.execute('''
                INSERT OR IGNORE INTO events 
                (event_id, happened_at, kind, canonical_id, service_name, raw_json)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (event_id, happened_at, kind, canonical_id, service_name, raw_json))

