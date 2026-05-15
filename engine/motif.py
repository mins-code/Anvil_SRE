from typing import Dict, List, Any

def extract_motif(causal_chain: List[Dict[str, Any]]) -> str:
    """
    Extract a structural motif from a causal chain.
    """
    if not causal_chain:
        return "Unknown"
        
    out_degree: Dict[str, int] = {}
    in_degree: Dict[str, int] = {}
    edges: List[tuple] = []
    
    for edge in causal_chain:
        cause = edge.get("cause_event_id")
        effect = edge.get("effect_event_id")
        
        if cause and effect:
            edges.append((cause, effect))
            out_degree[cause] = out_degree.get(cause, 0) + 1
            in_degree[effect] = in_degree.get(effect, 0) + 1
            if cause not in in_degree:
                in_degree[cause] = 0
            if effect not in out_degree:
                out_degree[effect] = 0
                
    if not edges:
        return "Unknown"

    if check_cycle(edges):
        return "Circular Dependency"

    max_out = max(out_degree.values()) if out_degree else 0
    if max_out >= 3:
        return "Star / Single Point of Failure"

    max_in = max(in_degree.values()) if in_degree else 0
    if max_out <= 1 and max_in <= 1 and len(edges) >= 2:
        return "Cascade Failure"

    if len(edges) >= 2:
        return "Cascade Failure"

    return "Direct Effect"

def check_cycle(edges: List[tuple]) -> bool:
    graph: Dict[str, List[str]] = {}
    for u, v in edges:
        if u not in graph:
            graph[u] = []
        graph[u].append(v)
        
    visited = set()
    rec_stack = set()
    
    def dfs(node):
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if dfs(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True
        rec_stack.remove(node)
        return False
        
    for node in graph:
        if node not in visited:
            if dfs(node):
                return True
    return False

def get_motif_name(motif: str) -> str:
    return motif
