// Options > Mods lists the native mods: this one is the Shader Options page.
fn options_mods(
    game: &mut crate::game::Game<'_>,
    mods: &mut Vec<crate::game::native_hooks::NativeMod>,
) {
    crate::game::pack_options::on_mods(game, mods);
}
