import json
import dataclasses
from pathlib import Path
from typing import List, Optional
from schema import CodeNode, OntologyConfig


class ContextCompiler:

    @staticmethod
    def compile(focal_target: str, nodes: List[CodeNode], resolution: str,
                direction: str, radius: int, out_filepath: str,
                ontology: OntologyConfig) -> None:
        """
        Generates a deterministic markdown-formatted architectural context file
        from the traversed sub-graph.
        """
        out_path = Path(out_filepath)

        with open(out_path, "w", encoding="utf-8") as file:
            file.write("# Dynamic Architectural Context Compilation\n")
            file.write(
                "Generated deterministically by Celial Graph Engine.\n\n")

            file.write(
                "## 0. Active System Ontology Configuration (Boundary Constraints)\n"
            )
            file.write("```json\n")
            # Convert dataclass hierarchy cleanly to dictionary for serialization
            ontology_dict = dataclasses.asdict(ontology)
            file.write(json.dumps(ontology_dict, indent=2) + "\n")
            file.write("```\n\n")

            file.write("## 1. Architectural Context Bounds\n")
            file.write(f"- **Focal Target**: `{focal_target}`\n")
            file.write(f"- **Search Direction**: `{direction}`\n")
            file.write(f"- **Traversed Radius**: `{radius}` hops\n")
            file.write(f"- **Resolution Profile**: `{resolution}`\n")
            file.write(f"- **Total Resolved Nodes**: `{len(nodes)}`\n\n")

            file.write("---\n")
            file.write("## 2. Focal Core Target Node\n")

            # Emulating Rust's iterator slice search path
            core_node = next((n for n in nodes if n.id == focal_target), None)
            if core_node is not None:
                ContextCompiler._write_node_details(file, core_node,
                                                    resolution)
            else:
                file.write(
                    "_Error: Focal node was not compiled in the graph state database._\n"
                )

            file.write("---\n")
            file.write("## 3. Traversed Subgraph Neighbors\n\n")

            sibling_index = 0
            for node in nodes:
                if node.id == focal_target:
                    continue
                sibling_index += 1
                file.write(f"### Neighbor #{sibling_index}: `{node.id}`\n")

                node_res = "full" if resolution == "full" else "signature"
                ContextCompiler._write_node_details(file, node, node_res)

    @staticmethod
    def _write_node_details(writer, node: CodeNode, resolution: str) -> None:
        """Internal helper to stream structured code node metadata into the markdown generator."""
        writer.write(f"- **Symbol ID**: `{node.id}`\n")
        writer.write(f"- **Filepath**: `{node.filepath}`\n")
        # Accessing value property of the String-backed Enum
        writer.write(f"- **AST Type**: `'{node.node_type.value}'`\n")

        sem = node.semantics
        writer.write(
            f"- **Semantic Facets**: `[Layer: {sem.layer}, Role: {sem.role}, "
            f"Pattern: {sem.pattern}, Purity: {sem.purity}, Barrier: {sem.purity_barrier}]`\n"
        )

        summary_text = node.ai_summary if node.ai_summary is not None else "No cached AI summary available."
        writer.write(f"- **AI Summary**: _{summary_text}_\n")

        if resolution == "full":
            writer.write("\n#### Active Code Block:\n")
            code_block_lang = "rust" if node.filepath.endswith(
                ".rs") else "dart"
            writer.write(f"```{code_block_lang}\n")
            writer.write(f"{node.raw_code}\n")
            writer.write("```\n\n")
        else:
            writer.write(
                "\n_Signature resolution mode: active code block suppressed._\n\n"
            )
