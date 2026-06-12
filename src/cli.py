import argparse


def parse_args() -> argparse.Namespace:
    """
    Parses command-line arguments using subparsers to recreate 
    the Rust clap subcommand enum layout.
    """
    parser = argparse.ArgumentParser(
        description=
        "Celial Graph Engine Architectural Indexer Command Line Interface")

    subparsers = parser.add_subparsers(dest="command",
                                       required=True,
                                       help="Subcommands")

    # DiscoverOntology
    subparsers.add_parser(
        "discover-ontology",
        help="Discover active ontology axes and layer configuration topology.")

    # Init
    subparsers.add_parser(
        "init",
        help=
        "Bootstrap workspace environment and generate .celial/celial.json baseline."
    )

    # Reindex
    subparsers.add_parser(
        "reindex",
        help=
        "Scan git workspace state, identify structural changes, and update DB graph."
    )

    # GetDirtyNodes
    subparsers.add_parser(
        "get-dirty-nodes",
        help=
        "Retrieve a manifest of all nodes marked dirty requiring summary regeneration."
    )

    # CompileContext
    compile_parser = subparsers.add_parser(
        "compile-context",
        help=
        "Extract localized sub-graph neighbors and compile deterministic markdown context."
    )
    compile_parser.add_argument("--target",
                                required=True,
                                type=str,
                                help="Focal core node identifier.")
    compile_parser.add_argument("--radius",
                                default=1,
                                type=int,
                                help="Graph traversal hop boundary distance.")
    compile_parser.add_argument(
        "--direction",
        required=True,
        type=str,
        choices=["upstream", "downstream", "symmetric"])
    compile_parser.add_argument("--resolution",
                                required=True,
                                type=str,
                                choices=["full", "signature"])
    compile_parser.add_argument("--out",
                                required=True,
                                type=str,
                                help="Output destination file path path.")

    # Audit
    audit_parser = subparsers.add_parser(
        "audit",
        help=
        "Audit architectural graph structures against policy boundary constraints."
    )
    audit_parser.add_argument("--rule",
                              default="all",
                              type=str,
                              help="Specific constraint rule validation key.")

    # QueryFile
    query_parser = subparsers.add_parser(
        "query-file",
        help=
        "Query specific workspace file entity allocations directly from database state."
    )
    query_parser.add_argument("--path",
                              required=True,
                              type=str,
                              help="Target codebase file path string.")
    query_parser.add_argument("--methods",
                              action="store_true",
                              help="Filter and isolate method AST types.")
    query_parser.add_argument("--independent-functions",
                              action="store_true",
                              help="Isolate detached non-impl functions.")
    query_parser.add_argument(
        "--impl-methods",
        type=str,
        default=None,
        help="Target specific struct implementation block name.")
    query_parser.add_argument("--classes",
                              action="store_true",
                              help="Isolate class or struct declarations.")
    query_parser.add_argument("--functions",
                              action="store_true",
                              help="Isolate general function symbols.")
    query_parser.add_argument("--imports",
                              action="store_true",
                              help="Isolate import statement nodes.")
    query_parser.add_argument("--return-types",
                              type=str,
                              default=None,
                              help="Match specific return types.")
    query_parser.add_argument("--return-types-include",
                              type=str,
                              default=None,
                              help="Partial match return types fragment.")
    query_parser.add_argument(
        "--include-body",
        action="store_true",
        help="Toggle inclusion of entire raw block body text.")

    # UpdateNodes
    update_parser = subparsers.add_parser(
        "update-nodes",
        help=
        "Publish batch node metadata update payload to the local system IPC queue daemon."
    )
    update_parser.add_argument(
        "--payload-file",
        required=True,
        type=str,
        help="File path to the update JSON array source.")

    # SemanticSearch
    search_parser = subparsers.add_parser(
        "semantic-search",
        help=
        "Execute vector space similarity searches or fallback substring scans across the graph."
    )
    search_parser.add_argument("--query",
                               required=True,
                               type=str,
                               help="Natural language text target.")
    search_parser.add_argument(
        "--limit",
        default=5,
        type=int,
        help="Maximum number of returned target matches.")

    # Watch
    subparsers.add_parser(
        "watch",
        help=
        "Spin up persistent folder monitoring daemon to capture and sync ongoing structural mutations."
    )

    return parser.parse_args()
