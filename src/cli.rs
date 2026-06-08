use clap::{Parser, Subcommand};

#[derive(Parser, Debug)]
#[command(name = "arch_indexer")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Commands,
}

#[derive(Subcommand, Debug)]
pub enum Commands {
    /// Discover the dynamic taxonomy and currently recognized semantic tags
    DiscoverOntology,

    CompileContext {
        #[arg(long)]
        target: String,

        #[arg(long, default_value_t = 1)]
        radius: u8,

        #[arg(long)]
        direction: String,

        #[arg(long)]
        resolution: String,

        #[arg(long)]
        out: String,
    },

    Reindex {
        #[arg(long, default_value = ".")]
        path: String,
    },

    Audit {
        #[arg(long, default_value = "all")]
        rule: String,
    },

    /// List all nodes flagged as dirty (out of date) because of AST changes
    GetDirtyNodes,

    QueryFile {
        #[arg(long)]
        path: String,

        #[arg(long)]
        methods: bool,

        #[arg(long)]
        independent_functions: bool,

        #[arg(long)]
        impl_methods: Option<String>,

        #[arg(long)]
        classes: bool,

        #[arg(long)]
        functions: bool,

        #[arg(long)]
        imports: bool,

        #[arg(long)]
        return_types: Option<String>,

        #[arg(long)]
        return_types_include: Option<String>,
    },

    /// Update a specific node's metadata, clearing the dirty flag
    UpdateNode {
        #[arg(long)]
        id: String,

        #[arg(long)]
        summary: Option<String>,

        #[arg(long)]
        layer: Option<String>,

        #[arg(long)]
        role: Option<String>,

        #[arg(long)]
        pattern: Option<String>,

        #[arg(long)]
        purity: Option<String>,
    },
}
