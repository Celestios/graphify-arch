pub mod domain;
pub mod ui;

pub fn process(v: String) {
    ui::render(&v);
}