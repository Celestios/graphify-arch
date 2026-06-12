from pathlib import Path
from typing import Union

def resolve_relative_path(path: Union[str, Path, None], root: Path) -> str:
    """Normalizes a path relative to the root directory as a posix path."""
    if not path:
        return ""
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return Path(path).as_posix()
