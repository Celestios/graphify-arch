import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Tuple, Optional, Dict
import blake3
from tree_sitter import Language, Parser, Query, Node as TsNode

from schema import (AstNodeType, CodeNode, SemanticFacets, UnresolvedRelation,
                    ContainsRelation, CallsRelation, ImplementsRelation,
                    FfiBridgeRelation, FfiExportRelation)

# Initialize Tree-Sitter Language Grammars
RUST_LANG = None
DART_LANG = None

try:
    import tree_sitter_rust as ts_rust
    RUST_LANG = Language(ts_rust.language())
except ImportError:
    pass

try:
    import tree_sitter_dart as ts_dart
    DART_LANG = Language(ts_dart.language())
except ImportError:
    pass


class LanguageParser(ABC):
    """Abstract strategy for language-specific parser implementations."""

    @property
    @abstractmethod
    def extension(self) -> str:
        """The file extension handled by this parser."""
        pass

    @property
    @abstractmethod
    def query_name(self) -> str:
        """The query scm file name representing this language parser."""
        pass

    @abstractmethod
    def get_language(self) -> Optional[Language]:
        """Returns the tree-sitter Language instance."""
        pass

    @abstractmethod
    def get_node_type_for_class(self) -> AstNodeType:
        """Determines the semantic AST node type mapped to class definitions."""
        pass


class RustParser(LanguageParser):
    @property
    def extension(self) -> str:
        return "rs"

    @property
    def query_name(self) -> str:
        return "rust"

    def get_language(self) -> Optional[Language]:
        return RUST_LANG

    def get_node_type_for_class(self) -> AstNodeType:
        return AstNodeType.STRUCT


class DartParser(LanguageParser):
    @property
    def extension(self) -> str:
        return "dart"

    @property
    def query_name(self) -> str:
        return "dart"

    def get_language(self) -> Optional[Language]:
        return DART_LANG

    def get_node_type_for_class(self) -> AstNodeType:
        return AstNodeType.CLASS


PARSER_STRATEGIES: Dict[str, LanguageParser] = {
    "rs": RustParser(),
    "dart": DartParser(),
}


def handle_class_node(parser_cls, ts_node, filepath, file_node_id, content_bytes, strategy, nodes, relations, resolve_active_source):
    name_node = parser_cls._find_child_by_kind(ts_node, "type_identifier")
    if name_node:
        name = parser_cls._get_node_text(name_node, content_bytes)
        node_id = f"{filepath}::{name}"
        node_type = strategy.get_node_type_for_class()
        nodes.append(parser_cls._build_code_node(node_id, filepath, node_type, ts_node, content_bytes))
        relations.append(ContainsRelation(source_id=file_node_id, target_id=node_id))


def handle_function_node(parser_cls, ts_node, filepath, file_node_id, content_bytes, strategy, nodes, relations, resolve_active_source):
    name_node = parser_cls._find_child_by_kind(ts_node, "identifier")
    if name_node:
        name = parser_cls._get_node_text(name_node, content_bytes)
        node_id = f"{file_node_id}::{name}"
        nodes.append(parser_cls._build_code_node(node_id, filepath, AstNodeType.FUNCTION, ts_node, content_bytes))
        relations.append(ContainsRelation(source_id=file_node_id, target_id=node_id))


def handle_impl_target_node(parser_cls, ts_node, filepath, file_node_id, content_bytes, strategy, nodes, relations, resolve_active_source):
    name_node = parser_cls._find_child_by_kind(ts_node, "type_identifier")
    if name_node:
        name = parser_cls._get_node_text(name_node, content_bytes)
        node_id = f"{filepath}::impl_{name}"
        nodes.append(parser_cls._build_code_node(node_id, filepath, AstNodeType.STRUCT, ts_node, content_bytes))
        relations.append(ContainsRelation(source_id=file_node_id, target_id=node_id))


def handle_calls_relation(parser_cls, ts_node, filepath, file_node_id, content_bytes, strategy, nodes, relations, resolve_active_source):
    symbol = parser_cls._get_node_text(ts_node, content_bytes)
    relations.append(CallsRelation(source_id=resolve_active_source(ts_node), target_symbol=symbol, caller_filepath=filepath))


def handle_implements_relation(parser_cls, ts_node, filepath, file_node_id, content_bytes, strategy, nodes, relations, resolve_active_source):
    symbol = parser_cls._get_node_text(ts_node, content_bytes)
    relations.append(ImplementsRelation(source_id=resolve_active_source(ts_node), target_symbol=symbol))


def handle_ffi_bridge_relation(parser_cls, ts_node, filepath, file_node_id, content_bytes, strategy, nodes, relations, resolve_active_source):
    symbol = parser_cls._get_node_text(ts_node, content_bytes)
    relations.append(FfiBridgeRelation(source_id=resolve_active_source(ts_node), target_symbol=symbol, caller_filepath=filepath))


def handle_ffi_export_relation(parser_cls, ts_node, filepath, file_node_id, content_bytes, strategy, nodes, relations, resolve_active_source):
    symbol = parser_cls._get_node_text(ts_node, content_bytes)
    relations.append(FfiExportRelation(source_id=resolve_active_source(ts_node), target_symbol=symbol))


CAPTURE_HANDLERS = {
    "node.class": handle_class_node,
    "node.function": handle_function_node,
    "node.impl_target": handle_impl_target_node,
    "relation.calls": handle_calls_relation,
    "relation.implements": handle_implements_relation,
    "relation.ffi_bridge": handle_ffi_bridge_relation,
    "node.ffi_export": handle_ffi_export_relation,
}


class AstParser:

    @staticmethod
    def locate_query(lang: str) -> str:
        """Recursively resolves the location of Tree-Sitter SCM query files."""
        primary_path = Path(f"queries/{lang}.scm")
        if primary_path.exists():
            return primary_path.read_text(encoding="utf-8")

        current_dir = Path.cwd().resolve()
        for parent in [current_dir] + list(current_dir.parents):
            nested_query = parent / "queries" / f"{lang}.scm"
            if nested_query.exists():
                return nested_query.read_text(encoding="utf-8")

        raise FileNotFoundError(
            f"Critical: Query rules asset for '{lang}' could not be located in local topology."
        )

    @classmethod
    def parse_file(
            cls, filepath: str,
            content: str) -> Tuple[List[CodeNode], List[UnresolvedRelation]]:
        """Parses target source code and yields isolated structural nodes and logical edges."""
        suffix = Path(filepath).suffix.lstrip(".")
        strategy = PARSER_STRATEGIES.get(suffix)

        if not strategy:
            raise ValueError(f"Unsupported file extension: .{suffix}")

        lang = strategy.get_language()
        if lang is None:
            raise ValueError(f"{strategy.query_name.capitalize()} tree-sitter grammar is not installed.")

        query_src = cls.locate_query(strategy.query_name)
        return cls._exec_query(filepath, content, query_src, lang, strategy)

    @classmethod
    def _exec_query(
            cls, filepath: str, content: str, query_src: str,
            language: Language, strategy: LanguageParser
    ) -> Tuple[List[CodeNode], List[UnresolvedRelation]]:
        parser = Parser(language)
        # Tree-sitter expects source encoding bytes for correct index tracking
        content_bytes = content.encode("utf-8")
        tree = parser.parse(content_bytes)
        root = tree.root_node

        nodes: List[CodeNode] = []
        relations: List[UnresolvedRelation] = []
        file_node_id = filepath

        # Bootstrap structural file boundary node
        nodes.append(
            CodeNode(id=file_node_id,
                     filepath=filepath,
                     node_type=AstNodeType.FILE,
                     start_byte=0,
                     end_byte=len(content_bytes),
                     ast_hash=cls.hash_normalized(root, content_bytes),
                     semantics=SemanticFacets(),
                     raw_code=content,
                     is_dirty=True))

        query = Query(language, query_src)
        captures = query.captures(root)

        def resolve_active_source(ts_node: TsNode) -> str:
            """Traverses backward up the processed node matrix to locate scope ownership."""
            for n in reversed(nodes):
                if (ts_node.start_byte >= n.start_byte
                        and ts_node.end_byte <= n.end_byte and n.node_type
                        in (AstNodeType.FUNCTION, AstNodeType.METHOD,
                            AstNodeType.STRUCT, AstNodeType.CLASS)):
                    return n.id
            return file_node_id

        # Tree-sitter Python captures return a dictionary/list of tuples: (Node, capture_name)
        for ts_node, capture_name in captures:
            handler = CAPTURE_HANDLERS.get(capture_name)
            if handler:
                handler(cls, ts_node, filepath, file_node_id, content_bytes, strategy, nodes, relations, resolve_active_source)

        return nodes, relations

    @staticmethod
    def hash_normalized(root_node: TsNode, content_bytes: bytes) -> str:
        """
        Executes a deterministic non-whitespace token-level hash calculation
        over the node's AST layout to eliminate formatting variances.
        """
        hasher = blake3.blake3()
        visit_stack = [root_node]

        while visit_stack:
            current = visit_stack.pop()

            # Check for structural named children that aren't comments
            has_named_children = any(child.is_named and child.type != "comment"
                                     for child in current.children)

            if not has_named_children and current.is_named and current.type != "comment":
                start = current.start_byte
                end = current.end_byte

                if end <= len(content_bytes):
                    token_bytes = content_bytes[start:end]
                    # Filter structural whitespace characters out of the calculation pipeline
                    normalized_bytes = bytes([
                        b for b in token_bytes if b not in (32, 10, 13, 9)
                    ])  # ' ', '\n', '\r', '\t'
                    hasher.update(normalized_bytes)

            # Mirror Rust stack execution bounds by reversing child nodes traversal sequence
            for child in reversed(current.children):
                visit_stack.append(child)

        return hasher.hexdigest()

    @staticmethod
    def _get_node_text(ts_node: TsNode, content_bytes: bytes) -> str:
        start = ts_node.start_byte
        end = ts_node.end_byte
        if end <= len(content_bytes):
            return content_bytes[start:end].decode("utf-8", errors="ignore")
        return ""

    @staticmethod
    def _find_child_by_kind(ts_node: TsNode, kind: str) -> Optional[TsNode]:
        for child in ts_node.children:
            if child.type == kind:
                return child
        return None

    @classmethod
    def _build_code_node(cls, node_id: str, filepath: str,
                         node_type: AstNodeType, ts_node: TsNode,
                         content_bytes: bytes) -> CodeNode:
        start = ts_node.start_byte
        end = ts_node.end_byte
        raw_segment = content_bytes[start:end].decode("utf-8", errors="ignore")
        ast_hash = cls.hash_normalized(ts_node, content_bytes)

        return CodeNode(id=node_id,
                        filepath=filepath,
                        node_type=node_type,
                        start_byte=start,
                        end_byte=end,
                        ast_hash=ast_hash,
                        semantics=SemanticFacets(),
                        raw_code=raw_segment,
                        is_dirty=True)

