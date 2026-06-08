use ort::{
    GraphOptimizationLevel, Session,
    session::builder::GraphOptimizationLevel as OptLevel,
};
use std::path::Path;
use tokenizers::Tokenizer;

pub struct LocalEmbedder {
    session: Session,
    tokenizer: Tokenizer,
}

impl LocalEmbedder {
    pub fn new(model_dir: &str) -> Result<Self, String> {
        let model_path = Path::new(model_dir).join("model.onnx");
        let tokenizer_path = Path::new(model_dir).join("tokenizer.json");

        if !model_path.exists() || !tokenizer_path.exists() {
            return Err(
                "Embedding model not found. Run `arch_indexer setup --download-model`".into(),
            );
        }

        let tokenizer = Tokenizer::from_file(tokenizer_path)
            .map_err(|e| format!("Tokenizer error: {}", e))?;

        let session = Session::builder()
            .map_err(|e| e.to_string())?
            .with_optimization_level(OptLevel::Level3)
            .map_err(|e| e.to_string())?
            .commit_from_file(model_path)
            .map_err(|e| e.to_string())?;

        Ok(Self {
            session,
            tokenizer,
        })
    }

    pub fn embed(&self, text: &str) -> Result<Vec<f32>, String> {
        let encoding = self.tokenizer.encode(text, true)
            .map_err(|e| format!("Encoding failed: {}", e))?;

        let input_ids = encoding
            .get_ids()
            .iter()
            .map(|&x| x as i64)
            .collect::<Vec<_>>();
        let attention_mask = encoding
            .get_attention_mask()
            .iter()
            .map(|&x| x as i64)
            .collect::<Vec<_>>();

        let seq_len = input_ids.len();
        let input_ids_tensor = ort::Value::from_array(
            ndarray::Array2::from_shape_vec((1, seq_len), input_ids).unwrap(),
        )
        .unwrap();
        let mask_tensor = ort::Value::from_array(
            ndarray::Array2::from_shape_vec((1, seq_len), attention_mask).unwrap(),
        )
        .unwrap();

        let outputs = self
            .session
            .run(ort::inputs! {
                "input_ids" => input_ids_tensor,
                "attention_mask" => mask_tensor,
            }
            .unwrap())
            .map_err(|e| e.to_string())?;

        let embeddings_tensor = outputs["last_hidden_state"].try_extract_tensor::<f32>().unwrap();
        let embeddings_view = embeddings_tensor.view();

        let mut pooled = vec![0.0f32; 768];
        for i in 0..seq_len {
            for d in 0..768 {
                pooled[d] += embeddings_view[[0, i, d]];
            }
        }
        for d in 0..768 {
            pooled[d] /= seq_len as f32;
        }

        let norm: f32 = pooled.iter().map(|x| x * x).sum::<f32>().sqrt();
        if norm > 0.0 {
            for d in 0..768 {
                pooled[d] /= norm;
            }
        }

        Ok(pooled)
    }
}
