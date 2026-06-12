import json
import dataclasses
from pathlib import Path
from typing import List, Optional
from schema import CodeNode, OntologyConfig


class ContextCompiler:

    @staticmethod
    def generate_markdown(focal_target: str, nodes: List[CodeNode], resolution: str,
                          direction: str, radius: int, ontology: OntologyConfig) -> str:
        """
        Generates a deterministic markdown-formatted architectural context string
        from the traversed sub-graph.
        """
        import io
        writer = io.StringIO()
        writer.write("# Dynamic Architectural Context Compilation\n")
        writer.write("Generated deterministically by Celial Graph Engine.\n\n")

        writer.write("## 0. Active System Ontology Configuration (Boundary Constraints)\n")
        writer.write("```json\n")
        ontology_dict = dataclasses.asdict(ontology)
        writer.write(json.dumps(ontology_dict, indent=2) + "\n")
        writer.write("```\n\n")

        writer.write("## 1. Architectural Context Bounds\n")
        writer.write(f"- **Focal Target**: `{focal_target}`\n")
        writer.write(f"- **Search Direction**: `{direction}`\n")
        writer.write(f"- **Traversed Radius**: `{radius}` hops\n")
        writer.write(f"- **Resolution Profile**: `{resolution}`\n")
        writer.write(f"- **Total Resolved Nodes**: `{len(nodes)}`\n\n")

        writer.write("---\n")
        writer.write("## 2. Focal Core Target Node\n")

        core_node = next((n for n in nodes if n.id == focal_target), None)
        if core_node is not None:
            ContextCompiler._write_node_details(writer, core_node, resolution)
        else:
            writer.write("_Error: Focal node was not compiled in the graph state database._\n")

        writer.write("---\n")
        writer.write("## 3. Traversed Subgraph Neighbors\n\n")

        sibling_index = 0
        for node in nodes:
            if node.id == focal_target:
                continue
            sibling_index += 1
            writer.write(f"### Neighbor #{sibling_index}: `{node.id}`\n")

            node_res = "full" if resolution == "full" else "signature"
            ContextCompiler._write_node_details(writer, node, node_res)

        return writer.getvalue()

    @staticmethod
    def compile(focal_target: str, nodes: List[CodeNode], resolution: str,
                direction: str, radius: int, out_filepath: str,
                ontology: OntologyConfig) -> None:
        """
        Generates a deterministic markdown-formatted architectural context file
        from the traversed sub-graph.
        """
        md_content = ContextCompiler.generate_markdown(focal_target, nodes, resolution, direction, radius, ontology)
        out_path = Path(out_filepath)
        out_path.write_text(md_content, encoding="utf-8")

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
