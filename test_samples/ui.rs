pub fn render(value: &str) {
    println!("{}", value);
}

pub fn tick() {
    crate::domain::save_state(&mut String::new(), "io");
}