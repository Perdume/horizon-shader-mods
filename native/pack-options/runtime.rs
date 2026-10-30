// pack_options: Options > Mods > Shader Options. One page for the shader pack in use: the packs of
// shaderpacks/ to apply, then its settings in the groups the pack itself puts them in (its `screen`
// lists), the water ones first. A click on an option gives it its next value, keeps the choice in
// shader-options/<pack>.txt and has the game load the pack again. pack_connect writes the choices into
// the pack's files as they are read.
//
// The page is Horizon's own mod page: the game asks `native_mods` for the mods to list and
// `native_settings` / `native_settings_click` for a mod's buttons (native_hooks.rs), and asks for the
// buttons again after every click, which is how a click on a group opens it.
//
// A support module's source takes no file attributes, so these are plain comments.

use std::sync::{Mutex, MutexGuard, OnceLock};

use horizon_shaders::{Macros, Pack, Properties};

use super::Game;
use super::native_hooks::NativeMod;
use super::pack_options_logic as logic;

const ID: &str = "pack_options";

#[derive(Clone)]
enum Action {
    Nothing,
    /// The option at this place of the options list gets its next value.
    Cycle(usize),
    Reset,
    /// Open a group of settings.
    Open(String),
    Back,
    /// Apply the pack (or turn it off when it is the one in use).
    Pick(String),
}

#[derive(Default)]
struct State {
    /// The pack the options below are those of.
    pack: String,
    loaded: bool,
    /// Why there are no options, or empty.
    note: String,
    opts: Vec<logic::Opt>,
    screens: logic::Screens,
    lang: logic::Lang,
    /// The groups opened so far; empty on the first page.
    path: Vec<String>,
    /// What each button of the page does, as the page was last asked for.
    actions: Vec<Action>,
}

fn state() -> MutexGuard<'static, State> {
    static STATE: OnceLock<Mutex<State>> = OnceLock::new();
    STATE
        .get_or_init(|| Mutex::new(State::default()))
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}

fn is_source(path: &str) -> bool {
    [".glsl", ".fsh", ".vsh", ".inc", ".h", ".csh", ".gsh"]
        .iter()
        .any(|suffix| path.ends_with(suffix))
}

/// Reads the options of `picked`, a pack of `shaderpacks/`.
fn load(s: &mut State, picked: &str) {
    *s = State::default();
    s.pack = picked.to_owned();
    s.loaded = true;
    if picked.is_empty() {
        s.note = "No shader pack is applied".to_owned();
        return;
    }
    let path = Game::shader_packs_dir().join(picked);
    let Ok(pack) = Pack::open(&path) else {
        s.note = format!("{picked}: no options here (not an Iris/OptiFine-format pack)");
        return;
    };
    let texts: Vec<String> = pack
        .all_files()
        .iter()
        .filter(|f| is_source(f))
        .filter_map(|f| pack.read_text(f))
        .collect();
    s.opts = logic::discover(texts.iter().map(String::as_str));
    let properties = Properties::parse(
        &pack.read_text("shaders.properties").unwrap_or_default(),
        &Macros::standard(),
    );
    s.screens = logic::Screens::parse(
        properties
            .entries
            .iter()
            .map(|e| (e.key.as_str(), e.value.as_str())),
    );
    s.lang = ["lang/en_US.lang", "lang/en_us.lang"]
        .iter()
        .find_map(|f| pack.read_text(f))
        .map(|text| logic::Lang::parse(&text))
        .unwrap_or_default();
    if s.opts.is_empty() {
        s.note = format!("{picked}: declares no options");
    }
}

/// Options > Mods lists this page.
pub(super) fn on_mods(_game: &mut Game<'_>, mods: &mut Vec<NativeMod>) {
    mods.push(NativeMod {
        id: ID.to_owned(),
        title: "Shader Options".to_owned(),
        description: "Apply a shader pack and change its settings: water, lighting, shadows..."
            .to_owned(),
        keys: Vec::new(),
    });
}

fn add(buttons: &mut Vec<String>, actions: &mut Vec<Action>, text: String, action: Action) {
    buttons.push(text);
    actions.push(action);
}

/// The buttons of the page, each time it opens and after each click.
pub(super) fn on_settings(game: &mut Game<'_>, mod_id: &str, buttons: &mut Vec<String>) {
    if mod_id != ID {
        return;
    }
    let picked = game.settings.horizon.shader_pack.clone();
    let mut guard = state();
    if !guard.loaded || guard.pack != picked {
        load(&mut guard, &picked);
    }
    let s = &mut *guard;
    s.actions.clear();
    if s.path.is_empty() {
        let head = if s.note.is_empty() {
            format!("{}: {} settings", s.pack, s.opts.len())
        } else {
            s.note.clone()
        };
        add(buttons, &mut s.actions, head, Action::Nothing);
        let packs = horizon_client::shader_pack_list::scan(&Game::shader_packs_dir());
        if packs.is_empty() {
            let text = "No packs in the shaderpacks folder".to_owned();
            add(buttons, &mut s.actions, text, Action::Nothing);
        }
        for pack in packs {
            let mark = if pack.file == picked { "ON" } else { "OFF" };
            let text = format!("{}: {mark}", pack.title);
            add(buttons, &mut s.actions, text, Action::Pick(pack.file));
        }
        if !s.opts.is_empty() {
            let text = "- Settings -".to_owned();
            add(buttons, &mut s.actions, text, Action::Nothing);
        }
    } else {
        add(buttons, &mut s.actions, "< Back".to_owned(), Action::Back);
        let text = logic::crumbs(&s.path, &s.lang);
        add(buttons, &mut s.actions, text, Action::Nothing);
    }
    for entry in logic::entries(&s.screens, &s.opts, &s.path) {
        match entry {
            logic::Entry::Opt(i) => {
                let text = logic::label(&s.opts[i], &s.lang);
                add(buttons, &mut s.actions, text, Action::Cycle(i));
            }
            logic::Entry::Sub { name, count } => {
                let text = logic::sub_label(&name, count, &s.lang);
                add(buttons, &mut s.actions, text, Action::Open(name));
            }
        }
    }
    if s.path.is_empty() && !s.opts.is_empty() {
        let text = "Reset all options".to_owned();
        add(buttons, &mut s.actions, text, Action::Reset);
    }
}

/// Keeps a choice for `pack`.
fn save(pack: &str, name: &str, value: &str) {
    let file = logic::file_of(pack);
    let mut list = std::fs::read_to_string(&file)
        .map(|text| logic::parse_overrides(&text))
        .unwrap_or_default();
    logic::set_override(&mut list, name, value);
    let _ = std::fs::create_dir_all(logic::DIR);
    let _ = std::fs::write(&file, logic::write_overrides(&list));
}

/// The game loads the pack again on its next frame (what picking another pack does).
fn reload(game: &mut Game<'_>) {
    game.pack_ids = None;
    if let Some(renderer) = &mut game.renderer {
        renderer.drop_shader_packs();
    }
}

/// A button of the page was clicked.
pub(super) fn on_click(game: &mut Game<'_>, mod_id: &str, button: usize) {
    if mod_id != ID {
        return;
    }
    let mut s = state();
    match s.actions.get(button).cloned() {
        Some(Action::Cycle(i)) => {
            let Some(opt) = s.opts.get(i) else {
                return;
            };
            let value = logic::next(opt);
            let name = opt.name.clone();
            let pack = s.pack.clone();
            save(&pack, &name, &value);
            s.opts[i] = logic::with_value(&s.opts[i], &value);
        }
        Some(Action::Reset) => {
            let _ = std::fs::remove_file(logic::file_of(&s.pack));
            // The options as the pack declares them.
            s.loaded = false;
        }
        Some(Action::Open(name)) => {
            s.path.push(name);
            return;
        }
        Some(Action::Back) => {
            s.path.pop();
            return;
        }
        Some(Action::Pick(file)) => {
            drop(s);
            // What the Shader Packs page does: the pick is saved and the next frame loads it.
            let picked = &mut game.settings.horizon.shader_pack;
            *picked = if *picked == file { String::new() } else { file };
            game.settings.save(&Game::options_path());
            return;
        }
        Some(Action::Nothing) | None => return,
    }
    drop(s);
    reload(game);
}
