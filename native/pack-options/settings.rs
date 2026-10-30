// The buttons of a mod's settings page: those of the Shader Options page when it is ours.
fn options_settings(game: &mut crate::game::Game<'_>, mod_id: &str, buttons: &mut Vec<String>) {
    crate::game::pack_options::on_settings(game, mod_id, buttons);
}
