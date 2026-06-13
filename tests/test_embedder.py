import unittest
import sys
import tempfile
import shutil
import math
from pathlib import Path
from unittest.mock import patch

# Add src to system path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import embedder
from embedder import bootstrap_embedding_model, LocalEmbedder

class TestEmbedder(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_bootstrap_and_embed(self):
        # To avoid slow network downloads, we can copy the model/tokenizer from the actual home directory if cached
        actual_home = Path.home()
        actual_models_dir = actual_home / ".arch" / "models"
        
        temp_models_dir = self.temp_dir / ".arch" / "models"
        if actual_models_dir.exists():
            temp_models_dir.mkdir(parents=True, exist_ok=True)
            for file_name in ["model.onnx", "tokenizer.json"]:
                actual_file = actual_models_dir / file_name
                if actual_file.exists():
                    shutil.copy(actual_file, temp_models_dir / file_name)

        # Patch Path.home to return our temporary directory
        with patch("pathlib.Path.home", return_value=self.temp_dir):
            # 1. Test bootstrapping/downloading model assets (or using copied ones)
            model_path, tokenizer_path = bootstrap_embedding_model()
            
            self.assertTrue(model_path.exists())
            self.assertTrue(tokenizer_path.exists())
            self.assertEqual(model_path.name, "model.onnx")
            self.assertEqual(tokenizer_path.name, "tokenizer.json")
            
            # 2. Test initializing the embedder and embedding text
            local_embedder = LocalEmbedder()
            
            text = "Testing the architecture indexer embedding model."
            vector = local_embedder.embed(text)
            
            # Verify the output vector
            self.assertIsInstance(vector, list)
            for val in vector:
                self.assertIsInstance(val, float)
                
            # all-MiniLM-L6-v2 outputs a 384-dimensional dense vector
            self.assertEqual(len(vector), 384)
            
            # Verify L2 normalization (sum of squares is approx 1.0)
            squared_sum = sum(v * v for v in vector)
            self.assertAlmostEqual(squared_sum, 1.0, places=5)

if __name__ == "__main__":
    unittest.main()
