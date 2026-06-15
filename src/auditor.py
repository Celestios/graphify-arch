import sys
import networkx as nx
from pathlib import Path
from typing import List, Dict, Any
from schema import OntologyConfig
from db import Violation
from utils import resolve_relative_path

def audit_architecture_rules(G: nx.MultiDiGraph, ontology: OntologyConfig, root: Path) -> List[Violation]:
    """Runs structural audits against configured architectural rules on call/import graphs."""
    violations = []
    skipped_count = 0
    
    # 1. Run rule-based checking for dependency_check fields
    for u, v, edge_data in G.edges(data=True):
        relation = edge_data.get("type") or edge_data.get("relation") or ""
        if relation.lower() not in ["calls", "uses", "references", "implements", "ffibridge", "imports"]:
            continue
        
        # Detect reversed edges: edge's source_file identifies the referencer.
        # If the source node's file doesn't match, the edge direction is reversed.
        edge_sf = edge_data.get("source_file", "")
        u_sf = resolve_relative_path(G.nodes[u].get("source_file"), root)
        v_sf = resolve_relative_path(G.nodes[v].get("source_file"), root)
        edge_sf_rel = resolve_relative_path(edge_sf, root) if edge_sf else ""

        src, tgt = u, v
        if edge_sf_rel and edge_sf_rel == v_sf and edge_sf_rel != u_sf:
            src, tgt = v, u

        rel_u_sf = resolve_relative_path(G.nodes[src].get("source_file"), root)
        u_fields_cfg = ontology.get_fields_for_file(rel_u_sf)
        
        for field_name, field_config in u_fields_cfg.items():
            if field_config.handler != "dependency_check":
                continue
                
            src_val = G.nodes[src].get(f"arch_meta_{field_name}")
            tgt_val = G.nodes[tgt].get(f"arch_meta_{field_name}")
            
            if src_val is None or tgt_val is None:
                skipped_count += 1
                continue
                
            # Check rules
            for rule in field_config.rules:
                if src_val == rule.source and tgt_val == rule.target:
                    violations.append(Violation(
                        rule_name=f"{field_name}_violation",
                        source_id=src,
                        target_id=tgt,
                        filepath=G.nodes[src].get("source_file", ""),
                        start_byte=G.nodes[src].get("start_byte", 0),
                        end_byte=G.nodes[src].get("end_byte", 0),
                        message=rule.message
                    ))

    # 2. Check for components marked for audit (requires_audit = True)
    for node_id, data in G.nodes(data=True):
        if data.get("arch_meta_requires_audit"):
            violations.append(Violation(
                rule_name="requires_audit",
                source_id=node_id,
                target_id="",
                filepath=data.get("source_file", ""),
                start_byte=data.get("start_byte", 0),
                end_byte=data.get("end_byte", 0),
                message=f"Component '{node_id}' requires manual architecture audit due to code/ontology schema changes."
            ))
    
    if skipped_count > 0 and not violations:
        print(f"warning: {skipped_count} edge(s) skipped because node metadata was not assigned. Check your config.json assignment rules.", file=sys.stderr)
            
    return violations
