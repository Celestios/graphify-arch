import sys
import importlib.metadata
import logging
from pathlib import Path

logger = logging.getLogger("graphify_arch.plugins")

class PluginHookInterface:
    """Interface that plugins should implement to hook into Graphify-Arch."""
    name: str = "base_plugin"

    def should_activate(self, root: Path) -> bool:
        """Return True if this plugin should run for the target directory."""
        return False

    def on_post_extract(self, extraction: dict, root: Path) -> dict:
        """Modify or enrich the raw extraction payload (nodes & edges dict)."""
        return extraction

    def on_post_build(self, G, extraction: dict, root: Path):
        """Enrich the NetworkX graph object (G) after it's loaded from extraction."""
        return G

    def on_post_analyze(self, G, communities: dict, analysis: dict, root: Path) -> dict:
        """Enrich the analysis dict (surprising connections, god nodes, etc.)."""
        return analysis

    def on_export(self, G, communities: dict, analysis: dict, out_dir: Path) -> None:
        """Write auxiliary files or reports into the output directory."""
        pass

    def register_cli(self, subparsers) -> None:
        """Register subcommands into the Graphify-Arch CLI arg parser."""
        pass


def discover_plugins(root: Path) -> list[PluginHookInterface]:
    """Discover and return all active plugins using importlib entry points."""
    active_plugins = []
    
    # Query entry points registered under the group 'graphify_arch.plugins' and legacy 'graphify.plugins'
    for group in ["graphify_arch.plugins", "graphify.plugins"]:
        try:
            entry_points = importlib.metadata.entry_points(group=group)
        except Exception as e:
            logger.warning(f"Failed to query entry points for group {group}: {e}")
            continue

        for ep in entry_points:
            try:
                plugin_class = ep.load()
                instance = plugin_class()
                if isinstance(instance, PluginHookInterface) or hasattr(instance, "should_activate"):
                    if instance.should_activate(root):
                        logger.info(f"Loaded and activated plugin: {ep.name}")
                        active_plugins.append(instance)
                    else:
                        logger.debug(f"Plugin discovered but skipped (should_activate=False): {ep.name}")
            except Exception as e:
                logger.error(f"Failed to load plugin entry point {ep.name}: {e}", exc_info=True)
            
    return active_plugins


def run_hook(plugins: list, hook_name: str, *args, **kwargs):
    """Executes a hook across all plugins, chaining the first argument as accumulator."""
    result = args[0] if args else None
    remaining_args = args[1:] if len(args) > 1 else []
    
    for plugin in plugins:
        fn = getattr(plugin, hook_name, None)
        if fn:
            try:
                # Chain call: output of plugin N becomes input to plugin N+1
                out = fn(result, *remaining_args, **kwargs)
                if out is not None:
                    result = out
            except Exception as e:
                logger.error(f"Error executing hook '{hook_name}' in plugin '{getattr(plugin, 'name', type(plugin).__name__)}': {e}")
                
    return result
