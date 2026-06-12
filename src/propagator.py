import networkx as nx
from pathlib import Path
from schema import OntologyConfig

def propagate_purities(G: nx.MultiDiGraph, ontology: OntologyConfig, root: Path) -> nx.MultiDiGraph:
    """Propagates values for fields configured with 'propagation' handler."""
    # Find all field names that have a propagation handler in any directory
    propagation_fields = set()
    for dir_name, fields in ontology.directories.items():
        for name, field_cfg in fields.items():
            if field_cfg.handler == "propagation":
                propagation_fields.add(name)

    for field_name in propagation_fields:
        # For each node, we need to initialize default value if not set
        for node_id, node_data in G.nodes(data=True):
            sf = node_data.get("source_file") or ""
            if sf:
                try:
                    rel_sf = Path(sf).relative_to(root).as_posix()
                except ValueError:
                    rel_sf = Path(sf).as_posix()
            else:
                rel_sf = ""
            fields_cfg = ontology.get_fields_for_file(rel_sf)
            field_config = fields_cfg.get(field_name)
            if not field_config:
                continue
                
            val = node_data.get(f"arch_meta_{field_name}")
            if val is None or val == field_config.default:
                node_data[f"arch_meta_{field_name}"] = field_config.default

        # Propagation loop
        converged = False
        iterations = 0
        max_iterations = 100

        while not converged and iterations < max_iterations:
            changed = False
            iterations += 1

            for u in G.nodes():
                u_data = G.nodes[u]
                sf = u_data.get("source_file") or ""
                if sf:
                    try:
                        rel_sf = Path(sf).relative_to(root).as_posix()
                    except ValueError:
                        rel_sf = Path(sf).as_posix()
                else:
                    rel_sf = ""
                fields_cfg = ontology.get_fields_for_file(rel_sf)
                field_config = fields_cfg.get(field_name)
                if not field_config:
                    continue

                # Get weights mapping
                weights = field_config.weights
                if not weights:
                    # Fallback to values list index
                    weights = {val: idx for idx, val in enumerate(field_config.values)}
                reverse_weight_map = {v: k for k, v in weights.items()}

                barriers = set(field_config.barriers)
                barrier_field = field_config.barrier_field

                # Check barriers
                u_barrier_val = u_data.get(f"arch_meta_{barrier_field}", "")
                if u_barrier_val in barriers:
                    continue

                current_val = u_data.get(f"arch_meta_{field_name}", field_config.default)
                current_weight = weights.get(current_val, 0)
                max_callee_weight = current_weight

                edges_fn = G.out_edges if hasattr(G, "out_edges") else G.edges
                for _, v, edge_data in edges_fn(u, data=True):
                    relation = edge_data.get("type") or edge_data.get("relation") or ""
                    if relation.lower() not in ["calls", "uses", "references"]:
                        continue

                    v_data = G.nodes[v]
                    v_val = v_data.get(f"arch_meta_{field_name}")
                    if v_val is None:
                        v_sf = v_data.get("source_file") or ""
                        if v_sf:
                            try:
                                rel_v_sf = Path(v_sf).relative_to(root).as_posix()
                            except ValueError:
                                rel_v_sf = Path(v_sf).as_posix()
                        else:
                            rel_v_sf = ""
                        v_fields_cfg = ontology.get_fields_for_file(rel_v_sf)
                        v_field_config = v_fields_cfg.get(field_name)
                        v_val = v_field_config.default if v_field_config else None
                    
                    if v_val is not None:
                        v_weight = weights.get(v_val, 0)
                        if v_weight > max_callee_weight:
                            max_callee_weight = v_weight

                if max_callee_weight > current_weight:
                    u_data[f"arch_meta_{field_name}"] = reverse_weight_map.get(max_callee_weight, current_val)
                    changed = True

            if not changed:
                converged = True

    return G
