use clap::{Parser, Subcommand};

#[derive(Parser, Debug)]
#[command(name = "arch_indexer")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Subcommand, Debug)]
pub enum Commands {
    DiscoverOntology,
    Init,
    Reindex,
    GetDirtyNodes,

    CompileContext {
        #[arg(long)] target: String,
        #[arg(long, default_value_t = 1)] radius: u8,
        #[arg(long)] direction: String,
        #[arg(long)] resolution: String,
        #[arg(long)] out: String,
    },

    Audit {
        #[arg(long, default_value = "all")] rule: String,
    },

    QueryFile {
        #[arg(long)] path: String,
        #[arg(long)] methods: bool,
        #[arg(long)] independent_functions: bool,
        #[arg(long)] impl_methods: Option<String>,
        #[arg(long)] classes: bool,
        #[arg(long)] functions: bool,
        #[arg(long)] imports: bool,
        #[arg(long)] return_types: Option<String>,
        #[arg(long)] return_types_include: Option<String>,
        #[arg(long)] include_body: bool,
    },

    UpdateNodes {
        #[arg(long)] payload_file: String,
    },

    SemanticSearch {
        #[arg(long)] query: String,
        #[arg(long, default_value_t = 5)] limit: u32,
    },

    Watch,
}
