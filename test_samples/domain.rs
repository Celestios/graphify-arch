pub fn save_state(state: &mut String, value: &str) {
    state.push_str(value);
}

pub fn load_data() -> String {
    String::from("data")
}