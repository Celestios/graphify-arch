mod cli;
mod compiler;
mod db;
mod parser;
mod schema;

use clap::Parser;
use cli::{Cli, Commands};
use compiler::ContextCompiler;
use db::Database;
use parser::AstParser;
use schema::OntologyConfig;
use std::fs;
use std::path::Path;

fn load_ontology(path: &str) -> OntologyConfig {
    let content = fs::read_to_string(path).expect("Failed to read ontology configuration");
    let config: OntologyConfig = serde_json::from_str(&content).expect("Invalid ontology schema");
    if let Err(e) = config.validate() {
        eprintln!("Ontology boundary validation failed: {}", e);
        std::process::exit(1);
    }
    config
}

fn main() {
    let args = Cli::parse();
    let ontology = load_ontology("celial.json");

    let mut database = match Database::init(".celial_graph.db") {
        Ok(db) => db,
        Err(e) => {
            eprintln!("Database initialization failed: {:?}", e);
            return;
        }
    };

    match args.command {
        Commands::DiscoverOntology => {
            let dirty_count = database.count_dirty_nodes().unwrap_or(0);
            
            let ontology = serde_json::json!({
                "structural_axes": ["upstream", "downstream", "symmetric"],
                "layers": ["Tier1Ui", "Tier2Fsm", "Tier3Domain"],
                "roles": ["FfiBridge", "StateContainer", "LayoutWidget", "Contract", "Implementation", "Utility"],
                "patterns": ["Singleton", "Strategy", "Command", "FsmState", "Repository"],
                "purities": ["Pure", "StateMutator", "IoBound"],
                "rules": ["layer_violation", "direct_state_mutation"],
                "state_metrics": {
                    "pending_dirty_nodes": dirty_count
                }
            });

            println!("{}", serde_json::to_string_pretty(&ontology).unwrap());
        }
        Commands::CompileContext { target, radius, direction, resolution, out } => {
            match database.get_subgraph(&target, radius, &direction) {
                Ok(nodes) => {
                    // Check if focal target doesn't exist, and invoke the fuzzy suggester [4]
                    if nodes.is_empty() {
                        eprintln!("Focal target '{}' not found in database.", target);
                        if let Ok(suggestions) = database.fuzzy_suggest_target(&target) {
                            if !suggestions.is_empty() {
                                eprintln!("Did you mean one of these active graph nodes?");
                                for sug in suggestions {
                                    eprintln!(" - {}", sug);
                                }
                            }
                        }
                        std::process::exit(1);
                    }

                    if let Err(e) = ContextCompiler::compile(&target, &nodes, &resolution, &direction, radius, &out) {
                        eprintln!("Context compilation failure: {:?}", e);
                        std::process::exit(1);
                    } else {
                        println!("Context saved to: {}", out);
                    }
                }
                Err(e) => {
                    eprintln!("Traversal failure: {:?}", e);
                    std::process::exit(1);
                }
            }
        }
        Commands::Reindex { path } => {
            println!("Scanning directory: {}", path);
            let mut parsed_nodes = Vec::new();
            let mut parsed_relations = Vec::new();
            
            if let Err(e) = visit_dirs(Path::new(&path), &mut parsed_nodes, &mut parsed_relations) {
                eprintln!("Scanning error: {:?}", e);
                return;
            }

            let mut file_groups: std::collections::HashMap<String, Vec<schema::CodeNode>> = std::collections::HashMap::new();
            for node in parsed_nodes {
                file_groups.entry(node.filepath.clone()).or_default().push(node);
            }

            let mut sync_count = 0;
            for (filepath, nodes) in file_groups {
                if let Err(e) = database.sync_nodes(&filepath, &nodes) {
                    eprintln!("Failed syncing file {}: {:?}", filepath, e);
                } else {
                    sync_count += nodes.len();
                }
            }

            println!("Synchronized {} nodes. Resolving structural relationships...", sync_count);
            match database.resolve_and_save_relations(&parsed_relations) {
                Ok(_) => println!("Successfully resolved and saved scope-aware logic edges."),
                Err(e) => {
                    eprintln!("Failed resolving relationships: {:?}", e);
                    return;
                }
            }

            println!("Propagating topological semantics (with purity-barrier protection)...");
            match database.propagate_semantics(&ontology) {
                Ok(_) => println!("Topological propagation completed successfully."),
                Err(e) => eprintln!("Failed to run topological propagation: {:?}", e),
            }
        }
        Commands::Audit { rule } => {
            println!("Auditing codebase invariants against profile: {} ...", rule);
            match database.check_architectural_violations(&ontology) {
                Ok(violations) => {
                    if violations.is_empty() {
                        println!("Success: Architecture is clean. Zero policy violations discovered.");
                        std::process::exit(0);
                    } else {
                        eprintln!("\nDiscovered {} structural policy violations:", violations.len());
                        for (i, violation) in violations.iter().enumerate() {
                            eprintln!(
                                "\n[{}] Violation Type: {}\n  File: {}\n  Source: {}\n  Target: {}\n  Detail: {}",
                                i + 1,
                                violation.rule_name,
                                violation.filepath,
                                violation.source_id,
                                violation.target_id,
                                violation.message
                            );
                        }
                        std::process::exit(1);
                    }
                }
                Err(e) => {
                    eprintln!("Audit execution failed: {:?}", e);
                    std::process::exit(1);
                }
            }
        }
        Commands::GetDirtyNodes => {
            match database.get_dirty_nodes() {
                Ok(dirty_list) => {
                    let json_array = serde_json::json!(dirty_list);
                    println!("{}", serde_json::to_string_pretty(&json_array).unwrap());
                }
                Err(e) => {
                    eprintln!("Failed to fetch dirty nodes: {:?}", e);
                    std::process::exit(1);
                }
            }
        }
        Commands::QueryFile { path, methods, independent_functions, impl_methods, classes, functions, imports, return_types, return_types_include } => {
            println!("Querying database for: {}", path);
            match database.query_file(&path, methods, independent_functions, impl_methods.as_deref(), classes, functions, imports, return_types.as_deref(), return_types_include.as_deref()) {
                Ok(result_json) => println!("{}", result_json),
                Err(e) => { eprintln!("Query failed: {:?}", e); std::process::exit(1); }
            }
        }
        Commands::UpdateNode { id, summary, layer, role, pattern, purity } => {
            println!("Updating metadata for Node ID: {}", id);
            
            // Allow programmatic configuration of purity barriers from CLI
            let mut barrier_opt = None;
            if let Some(ref r_val) = role {
                if r_val == "FfiBridge" {
                    barrier_opt = Some(true); // Treat FfiBridges as purity barriers [6]
                }
            }

            match database.update_node_metadata(&id, summary, layer, role, pattern, purity, barrier_opt) {
                Ok(_) => {
                    println!("Successfully updated node state and cleared dirty tracking flags.");
                }
                Err(e) => {
                    eprintln!("Failed to update node metadata: {:?}", e);
                    std::process::exit(1);
                }
            }
        }
    }
}

fn visit_dirs(
    dir: &Path,
    all_nodes: &mut Vec<schema::CodeNode>,
    all_relations: &mut Vec<schema::UnresolvedRelation>,
) -> std::io::Result<()> {
    if dir.is_dir() {
        for entry in fs::read_dir(dir)? {
            let entry = entry?;
            let path = entry.path();
            if path.is_dir() {
                visit_dirs(&path, all_nodes, all_relations)?;
            } else {
                let path_str = path.to_string_lossy();
                if path_str.ends_with(".rs") || path_str.ends_with(".dart") {
                    if let Ok(content) = fs::read_to_string(&path) {
                        match AstParser::parse_file(&path_str, &content) {
                            Ok((mut nodes, mut relations)) => {
                                all_nodes.append(&mut nodes);
                                all_relations.append(&mut relations);
                            }
                            Err(e) => eprintln!("Parser error on file {}: {}", path_str, e),
                        }
                    }
                }
            }
        }
    }
    Ok(())
}
