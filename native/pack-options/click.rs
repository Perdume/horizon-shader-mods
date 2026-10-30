// A button of a mod's settings page was clicked: act on it when the page is ours.
fn options_click(game: &mut crate::game::Game<'_>, mod_id: &str, button: usize) {
    crate::game::pack_options::on_click(game, mod_id, button);
}
