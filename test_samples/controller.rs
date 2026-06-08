use crate::domain;

pub fn direct_ui_call_from_domain(domain_state: &mut String) {
    domain::save_state(domain_state, "violation");
}