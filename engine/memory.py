import duckdb
from engine.temporal_identity_graph import TemporalIdentityGraph

class Memory:
    def __init__(self):
        # Connect to an in-memory DuckDB database
        self.db = duckdb.connect(":memory:")
        self._initialize_tables()
        self.tig = TemporalIdentityGraph()

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
                incident_id      TEXT PRIMARY KEY,
                happened_at      TEXT,
                canonical_id     TEXT,
                trigger          TEXT,
                trigger_type     TEXT,
                trigger_metric   TEXT,
                fingerprint_json TEXT,
                causal_json      TEXT
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

            ts = event.get('ts')

            if change in ('rename', 'split', 'merge'):
                # Route to TIG (TemporalIdentityGraph) instance
                if hasattr(self, 'tig') and self.tig is not None:
                    if change == 'rename':
                        self.tig.rename(from_val, to_val, ts)
                    elif change == 'split':
                        self.tig.split(from_val, into_val, ts)
                    elif change == 'merge':
                        self.tig.merge(from_val, to_val, ts)
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
            elif kind == 'incident_signal':
                service_name = event.get('service') or event.get('trigger', '').split('/')[0].split(':')[1] if ':' in event.get('trigger', '') else None
            elif kind == 'remediation':
                service_name = event.get('target')
            
            # Resolve to canonical_id
            canonical_id = service_name
            if hasattr(self, 'tig') and self.tig is not None and service_name:
                # Use TIG lookup if available
                if hasattr(self.tig, 'lookup'):
                    canonical_id = self.tig.lookup(service_name, at_time=happened_at)

            self.db.execute('''
                INSERT OR IGNORE INTO events 
                (event_id, happened_at, kind, canonical_id, service_name, raw_json)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (event_id, happened_at, kind, canonical_id, service_name, raw_json))
            
            if kind == 'incident_signal':
                incident_id = event.get('incident_id')
                trigger = event.get('trigger', '')
                
                trigger_type = ''
                trigger_metric = ''
                if ':' in trigger and '/' in trigger:
                    trigger_type = trigger.split(':')[0]
                    trigger_metric = trigger.split('/')[1].split('>')[0].split('<')[0].split('=')[0]
                
                self.db.execute('''
                    INSERT OR IGNORE INTO incidents
                    (incident_id, happened_at, canonical_id, trigger, trigger_type, trigger_metric)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (incident_id, happened_at, canonical_id, trigger, trigger_type, trigger_metric))
                
            elif kind == 'remediation':
                incident_id = event.get('incident_id')
                action = event.get('action')
                target_id = canonical_id
                version = event.get('version')
                outcome = event.get('outcome')
                
                if target_id is None:
                    import warnings
                    warnings.warn(
                        f"Skipping remediation record for incident '{incident_id}': "
                        f"could not resolve a canonical_id for target service "
                        f"(raw target={event.get('target')!r}). "
                        "Check that the remediation event includes a valid 'target' field.",
                        stacklevel=2,
                    )
                    return
                
                self.db.execute('''
                    INSERT INTO remediations
                    (incident_id, action, target_id, version, outcome, happened_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (incident_id, action, target_id, version, outcome, happened_at))

    def get_events_in_window(self, canonical_id: str, ts: str, window_minutes: int = 20):
        import json
        from datetime import datetime, timedelta

        target_ids = {canonical_id}
        if hasattr(self, 'tig') and self.tig is not None:
            ancestors = self.tig.ancestors(canonical_id)
            if ancestors:
                target_ids.update(ancestors)
                
        id_list = list(target_ids)
        if not id_list:
            return []
            
        placeholders = ','.join(['?'] * len(id_list))
        
        query = f"""
            SELECT happened_at, raw_json 
            FROM events 
            WHERE canonical_id IN ({placeholders})
        """
        all_events = self.db.execute(query, id_list).fetchall()
        
        clean_ts = ts.replace('Z', '+00:00')
        try:
            target_time = datetime.fromisoformat(clean_ts)
        except ValueError:
            return []
            
        start_time = target_time - timedelta(minutes=window_minutes)
        
        valid_events = []
        for happened_at_str, raw_json in all_events:
            if not happened_at_str:
                continue
            try:
                event_time = datetime.fromisoformat(happened_at_str.replace('Z', '+00:00'))
                if start_time <= event_time <= target_time:
                    valid_events.append((event_time, json.loads(raw_json)))
            except (ValueError, json.JSONDecodeError):
                continue
                
        valid_events.sort(key=lambda x: x[0])
        return [e[1] for e in valid_events]

    def update_incident_record(self, incident_id: str, fingerprint: dict, causal_chain: list):
        import json
        fingerprint_json = json.dumps(fingerprint)
        causal_json = json.dumps(causal_chain)
        
        self.db.execute('''
            UPDATE incidents
            SET fingerprint_json = ?, causal_json = ?
            WHERE incident_id = ?
        ''', (fingerprint_json, causal_json, incident_id))

    def get_all_past_incidents(self, exclude_id: str):
        import json
        query = """
            SELECT incident_id, canonical_id, trigger, fingerprint_json, causal_json
            FROM incidents
            WHERE incident_id != ? AND fingerprint_json IS NOT NULL
        """
        results = self.db.execute(query, (exclude_id,)).fetchall()
        
        past_incidents = []
        for row in results:
            inc_id, can_id, trig, fp_json, causal_json_str = row
            try:
                fp_dict = json.loads(fp_json) if fp_json else {}
                causal_chain = json.loads(causal_json_str) if causal_json_str else []
                past_incidents.append({
                    "incident_id": inc_id,
                    "canonical_id": can_id,
                    "trigger": trig,
                    "fingerprint": fp_dict,
                    "causal_chain": causal_chain
                })
            except json.JSONDecodeError:
                pass
                
        return past_incidents

    def get_remediations_for_incident(self, incident_id: str):
        query = """
            SELECT action, target_id, version, outcome
            FROM remediations
            WHERE incident_id = ?
        """
        return self.db.execute(query, (incident_id,)).fetchall()

    def store_events_batch(self, events: list):
        self.db.execute("BEGIN")
        try:
            for event in events:
                self.store_event(event)
            self.db.execute("COMMIT")
            
            import json
            from engine.fingerprint import extract_fingerprint
            for event in events:
                if event.get('kind') == 'incident_signal':
                    incident_id = event.get('incident_id')
                    happened_at = event.get('ts') or event.get('happened_at')
                    trigger = event.get('trigger', '')
                    
                    row = self.db.execute("SELECT canonical_id FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
                    if row:
                        canonical_id = row[0]
                        events_in_window = self.get_events_in_window(canonical_id, happened_at, window_minutes=20)
                        fingerprint = extract_fingerprint(events_in_window, trigger, happened_at)
                        fingerprint_json = json.dumps(fingerprint)
                        
                        self.db.execute('''
                            UPDATE incidents
                            SET fingerprint_json = ?
                            WHERE incident_id = ?
                        ''', (fingerprint_json, incident_id))
        except Exception:
            self.db.execute("ROLLBACK")
            raise
