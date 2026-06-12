import networkx as nx
from pathlib import Path
from typing import List, Dict, Any
from schema import OntologyConfig
from db import Violation

def audit_architecture_rules(G: nx.MultiDiGraph, ontology: OntologyConfig, root: Path) -> List[Violation]:
    """Runs structural audits against configured architectural rules on call/import graphs."""
    violations = []
    
    # 1. Run rule-based checking for dependency_check fields
    for u, v, edge_data in G.edges(data=True):
        relation = edge_data.get("type") or edge_data.get("relation") or ""
        if relation.lower() not in ["calls", "uses", "references", "implements", "ffibridge", "imports"]:
            continue
            
        u_sf = G.nodes[u].get("source_file") or ""
        if u_sf:
            try:
                rel_u_sf = Path(u_sf).relative_to(root).as_posix()
            except ValueError:
                rel_u_sf = Path(u_sf).as_posix()
        else:
            rel_u_sf = ""
        u_fields_cfg = ontology.get_fields_for_file(rel_u_sf)
        
        for field_name, field_config in u_fields_cfg.items():
            if field_config.handler != "dependency_check":
                continue
                
            u_val = G.nodes[u].get(f"arch_meta_{field_name}")
            v_val = G.nodes[v].get(f"arch_meta_{field_name}")
            
            if u_val is None or v_val is None:
                continue
                
            # Check rules
            for rule in field_config.rules:
                if u_val == rule.source and v_val == rule.target:
                    violations.append(Violation(
                        rule_name=f"{field_name}_violation",
                        source_id=u,
                        target_id=v,
                        filepath=G.nodes[u].get("source_file", ""),
                        start_byte=G.nodes[u].get("start_byte", 0),
                        end_byte=G.nodes[u].get("end_byte", 0),
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
            
    return violations
