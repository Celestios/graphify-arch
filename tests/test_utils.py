import unittest
import sys
from pathlib import Path

# Add src to system path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils import resolve_relative_path

class TestUtils(unittest.TestCase):
    def test_resolve_relative_path_success(self):
        root = Path("/home/user/project")
        path = Path("/home/user/project/src/main.rs")
        result = resolve_relative_path(path, root)
        self.assertEqual(result, "src/main.rs")

    def test_resolve_relative_path_string(self):
        root = Path("C:/project")
        path = "C:/project/src/main.rs"
        result = resolve_relative_path(path, root)
        self.assertEqual(result, "src/main.rs")

    def test_resolve_relative_path_none(self):
        root = Path("/home/user/project")
        self.assertEqual(resolve_relative_path(None, root), "")

    def test_resolve_relative_path_outside_root(self):
        root = Path("/home/user/project")
        path = "/home/user/other/main.rs"
        # Since it's outside the root directory, it should fallback to posix path
        self.assertEqual(resolve_relative_path(path, root), "/home/user/other/main.rs")

if __name__ == "__main__":
    unittest.main()
