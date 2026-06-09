use ort::session::Session;
use ort::session::builder::GraphOptimizationLevel as OptLevel;
use ort::value::Tensor;
use tokenizers::Tokenizer;

pub struct LocalEmbedder {
    session: Session,
    tokenizer: Tokenizer,
}

impl LocalEmbedder {
    pub fn new() -> Result<Self, String> {
        let home_dir = dirs::home_dir().ok_or("Could not resolve OS home directory")?;
        let model_dir = home_dir.join(".celial").join("models");

        let model_path = model_dir.join("model.onnx");
        let tokenizer_path = model_dir.join("tokenizer.json");

        if !model_path.exists() || !tokenizer_path.exists() {
            return Err(format!("Embedding model missing. Expected at {:?}", model_dir));
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

    pub fn embed(&mut self, text: &str) -> Result<Vec<f32>, String> {
        let mut tokenizer = self.tokenizer.clone();
        tokenizer.with_truncation(Some(tokenizers::TruncationParams {
            max_length: 512,
            ..Default::default()
        })).map_err(|e| format!("Truncation config failed: {}", e))?;

        let encoding = tokenizer.encode(text, true)
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

        let input_ids_tensor = Tensor::from_array(([1usize, seq_len], input_ids))
            .map_err(|e| e.to_string())?;
        let mask_tensor = Tensor::from_array(([1usize, seq_len], attention_mask))
            .map_err(|e| e.to_string())?;

        let outputs = self
            .session
            .run(ort::inputs! {
                "input_ids" => input_ids_tensor,
                "attention_mask" => mask_tensor,
            })
            .map_err(|e| e.to_string())?;

        let (_, embeddings_data) = outputs[0]
            .try_extract_tensor::<f32>()
            .map_err(|e| e.to_string())?;

        let expected = 1usize * seq_len * 768;
        if embeddings_data.len() < expected {
            return Err("Output tensor shorter than expected".into());
        }

        let mut pooled = vec![0.0f32; 768];
        for i in 0..seq_len {
            let offset = i * 768;
            for d in 0..768 {
                pooled[d] += embeddings_data[offset + d];
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
