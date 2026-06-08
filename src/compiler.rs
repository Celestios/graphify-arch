use crate::schema::CodeNode;
use std::fs;
use std::io::Write;

pub struct ContextCompiler;

impl ContextCompiler {
    pub fn compile(
        focal_target: &str,
        nodes: &[CodeNode],
        resolution: &str,
        direction: &str,
        radius: u8,
        out_filepath: &str,
    ) -> std::io::Result<()> {
        let mut file = fs::File::create(out_filepath)?;

        writeln!(file, "# Dynamic Architectural Context Compilation")?;
        writeln!(file, "Generated deterministically by Celial Graph Engine.\n")?;
        writeln!(file, "## Architectural Context Bounds")?;
        writeln!(file, "- **Focal Target**: `{}`", focal_target)?;
        writeln!(file, "- **Search Direction**: `{}`", direction)?;
        writeln!(file, "- **Traversed Radius**: `{}` hops", radius)?;
        writeln!(file, "- **Resolution Profile**: `{}`", resolution)?;
        writeln!(file, "- **Total Resolved Nodes**: `{}`\n", nodes.len())?;

        writeln!(file, "---")?;
        writeln!(file, "## 1. Focal Core Target Node")?;

        if let Some(core_node) = nodes.iter().find(|n| n.id == focal_target) {
            Self::write_node_details(&mut file, core_node, resolution)?;
        } else {
            writeln!(file, "_Error: Focal node was not compiled in the graph state database._")?;
        }

        writeln!(file, "---")?;
        writeln!(file, "## 2. Traversed Subgraph Neighbors")?;

        let mut sibling_index = 0;
        for node in nodes {
            if node.id == focal_target {
                continue;
            }
            sibling_index += 1;
            writeln!(file, "### Neighbor #{}: `{}`", sibling_index, node.id)?;
            let node_res = if resolution == "full" && radius == 1 { "full" } else { "signature" };
            Self::write_node_details(&mut file, node, node_res)?;
        }

        Ok(())
    }

    fn write_node_details<W: Write>(
        writer: &mut W,
        node: &CodeNode,
        resolution: &str,
    ) -> std::io::Result<()> {
        writeln!(writer, "- **Symbol ID**: `{}`", node.id)?;
        writeln!(writer, "- **Filepath**: `{}`", node.filepath)?;
        writeln!(writer, "- **AST Type**: `{:?}`", node.node_type)?;
        writeln!(
            writer,
            "- **Semantic Facets**: `[Layer: {:?}, Role: {:?}, Pattern: {:?}, Purity: {:?}, Barrier: {}]`",
            node.semantics.layer, node.semantics.role, node.semantics.pattern, node.semantics.purity, node.semantics.purity_barrier
        )?;
        
        let summary_text = node.ai_summary.as_deref().unwrap_or("No cached AI summary available.");
        writeln!(writer, "- **AI Summary**: _{}_", summary_text)?;

        // Expose both previous and current code ranges to facilitate agent's delta summarization loop [5]
        if let Some(ref old_code) = node.previous_code {
            writeln!(writer, "\n#### Outdated Code Block (Prior to modification):")?;
            let code_block_lang = if node.filepath.ends_with(".rs") { "rust" } else { "dart" };
            writeln!(writer, "```{}", code_block_lang)?;
            writeln!(writer, "{}", old_code)?;
            writeln!(writer, "```")?;
        }

        if resolution == "full" {
            writeln!(writer, "\n#### Active Code Block:")?;
            let code_block_lang = if node.filepath.ends_with(".rs") { "rust" } else { "dart" };
            writeln!(writer, "```{}", code_block_lang)?;
            writeln!(writer, "{}", node.raw_code)?;
            writeln!(writer, "```\n")?;
        } else {
            writeln!(writer, "\n_Signature resolution mode: active code block suppressed._\n")?;
        }

        Ok(())
    }
}
