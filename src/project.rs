use crate::schema::ProjectConfig;
use std::fs;
use std::path::PathBuf;

pub struct Workspace {
    pub root_dir: PathBuf,
    pub db_path: PathBuf,
    pub config_path: PathBuf,
    pub config: ProjectConfig,
}

impl Workspace {
    /// Recursively walks up from the current directory to locate the .celial/celial.json anchor
    pub fn discover() -> Result<Self, String> {
        let mut current_dir = std::env::current_dir().map_err(|e| e.to_string())?;

        loop {
            let dot_celial = current_dir.join(".celial");
            let config_path = dot_celial.join("celial.json");

            if config_path.exists() {
                let content = fs::read_to_string(&config_path)
                    .map_err(|e| format!("Failed to read celial.json: {}", e))?;

                let config: ProjectConfig = serde_json::from_str(&content)
                    .map_err(|e| format!("Invalid celial.json schema inside .celial/: {}", e))?;

                config.ontology.validate()?;

                return Ok(Self {
                    root_dir: current_dir,
                    db_path: dot_celial.join("graph.db"),
                    config_path,
                    config,
                });
            }

            if !current_dir.pop() {
                return Err("Fatal: Not a Celial workspace. No '.celial/celial.json' anchor discovered in this or parent directories. Run 'arch_indexer init' to bootstrap this folder.".to_string());
            }
        }
    }

    /// Tests if a file path matches project exclusions
    pub fn is_excluded(&self, filepath: &str) -> bool {
        self.config
            .exclusions
            .iter()
            .any(|ex| filepath.contains(ex))
    }
}
