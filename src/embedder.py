from pathlib import Path
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer


def bootstrap_embedding_model() -> tuple[Path, Path]:
    """Resolves local storage bounds and retrieves the ONNX embedding model assets if needed."""
    import urllib.request
    home_dir = Path.home()
    model_dir = home_dir / ".celial" / "models"

    model_path = model_dir / "model.onnx"
    tokenizer_path = model_dir / "tokenizer.json"

    if not model_path.exists() or not tokenizer_path.exists():
        model_dir.mkdir(parents=True, exist_ok=True)
        MODEL_URL = "https://huggingface.co/Qdrant/all-MiniLM-L6-v2-onnx/resolve/main/model.onnx"
        TOKENIZER_URL = "https://huggingface.co/Qdrant/all-MiniLM-L6-v2-onnx/resolve/main/tokenizer.json"
        
        try:
            if not model_path.exists():
                print(f"Downloading ONNX model to {model_path} (one-time operation)...")
                urllib.request.urlretrieve(MODEL_URL, model_path)
            if not tokenizer_path.exists():
                print(f"Downloading tokenizer to {tokenizer_path}...")
                urllib.request.urlretrieve(TOKENIZER_URL, tokenizer_path)
        except Exception as e:
            raise RuntimeError(f"Failed to download embedding model: {e}")
            
    return model_path, tokenizer_path


class LocalEmbedder:

    def __init__(self):
        """
        Cold-boots the embedding environment by initializing the ONNX optimization pipeline.
        """
        model_path, tokenizer_path = bootstrap_embedding_model()

        try:
            self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        except Exception as e:
            raise RuntimeError(f"Tokenizer error: {e}")

        # Replicating Level 3 graph optimization configuration
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        try:
            self.session = ort.InferenceSession(str(model_path),
                                                sess_options=opts)
        except Exception as e:
            raise RuntimeError(f"ONNX Session compilation failed: {e}")


    def embed(self, text: str) -> list[float]:
        """
        Encodes source text, executes tensor graph evaluation, 
        and applies vectorized mean-pooling with L2 normalization constraints.
        """
        # Constrain context window boundary inline
        self.tokenizer.enable_truncation(max_length=512)

        try:
            encoding = self.tokenizer.encode(text)
        except Exception as e:
            raise RuntimeError(f"Encoding failed: {e}")

        # Construct 2D tensor matrices [1, sequence_length] directly
        input_ids = np.array([encoding.ids], dtype=np.int64)
        attention_mask = np.array([encoding.attention_mask], dtype=np.int64)

        try:
            outputs = self.session.run(None, {
                "input_ids": input_ids,
                "attention_mask": attention_mask
            })
        except Exception as e:
            raise RuntimeError(f"ONNX inference execution failed: {e}")

        # Outputs shape expected: (1, seq_len, 768)
        embeddings_data = outputs[0]

        if embeddings_data.ndim != 3 or embeddings_data.shape[2] not in (384, 768):
            raise ValueError(
                "Output tensor shape deviates from the expected 384 or 768-dimensional space."
            )

        # Vectorized Mean Pooling along the sequence dimension axis (axis 1)
        pooled = np.mean(embeddings_data, axis=1)[0]

        # Vectorized L2 Normalization constraint satisfaction
        norm = np.linalg.norm(pooled)
        if norm > 0.0:
            pooled = pooled / norm

        return pooled.tolist()
