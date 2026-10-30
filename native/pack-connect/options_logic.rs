// Shader pack options, the part that knows no game: which options the files of an Iris/OptiFine-format
// pack declare, what a click does to one, how a choice is written back into a line, and the file a
// player's choices are kept in.
//
// Formats are the public ones (the "shader settings" page of the shaders.properties documentation):
//   #define NAME                         a switch (commented out: off)
//   #define NAME value //[a b c] text    a value option, the list after `//`
//   const int NAME = value; //[a b c]    a constant exposed the same way
//   screen / screen.<name> = names       the options a pack puts on its screens
//   lang: option.NAME = label, value.NAME.VALUE = label
//
// This file is used twice, byte for byte: as pack_options' module in the game crate (the settings page)
// and as pack_connect_options' module in the shaders crate (the choices applied as a pack is read).
// tools/check_options_logic.py keeps the two copies equal.
//
// A support module's source takes no file attributes, so these are plain comments.

use std::collections::{HashMap, HashSet};

/// Where a player's choices for a pack are kept, relative to the game folder.
pub(super) const DIR: &str = "shader-options";

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) enum Kind {
    Switch(bool),
    Choice {
        current: String,
        choices: Vec<String>,
    },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Opt {
    pub(super) name: String,
    pub(super) kind: Kind,
}

/// Where a declaration can be changed in its line.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Edit {
    /// A `#define NAME` switch: the `//` in front (at `at`) when it is commented out, and the `#` at `hash`.
    Switch {
        commented: bool,
        at: usize,
        hash: usize,
    },
    /// The value of a value option or constant: bytes `start..end` of the line.
    Value { start: usize, end: usize },
}

fn is_ident(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// The values of a `[a b c]` list at the start of a comment.
fn choices(comment: &str) -> Option<Vec<String>> {
    let inner = comment.trim().strip_prefix('[')?;
    let inner = &inner[..inner.find(']')?];
    let list: Vec<String> = inner.split_whitespace().map(str::to_owned).collect();
    (!list.is_empty()).then_some(list)
}

/// The option a line declares, and where in the line to change it.
fn scan(line: &str) -> Option<(Opt, Edit)> {
    let lead = line.len() - line.trim_start().len();
    let trimmed = line.trim();
    let (commented, body) = match trimmed.strip_prefix("//") {
        Some(rest) => (true, rest.trim_start()),
        None => (false, trimmed),
    };
    if let Some(rest) = body.strip_prefix("#define") {
        if !rest.starts_with(char::is_whitespace) {
            return None;
        }
        let rest = rest.trim_start();
        let end = rest.find(|c: char| !is_ident(c)).unwrap_or(rest.len());
        let name = &rest[..end];
        let tail = &rest[end..];
        if name.is_empty() || tail.starts_with('(') {
            return None;
        }
        // Byte offsets in the original line.
        let hash = line.find("#define")?;
        let after_define = hash + "#define".len();
        let name_end = after_define + line[after_define..].find(name)? + name.len();
        let (value_part, comment) = match tail.find("//") {
            Some(i) => (&tail[..i], Some(&tail[i + 2..])),
            None => (tail, None),
        };
        let value = value_part.trim();
        if value.is_empty() {
            let at = if commented { lead } else { hash };
            return Some((
                Opt {
                    name: name.to_owned(),
                    kind: Kind::Switch(!commented),
                },
                Edit::Switch {
                    commented,
                    at,
                    hash,
                },
            ));
        }
        if commented || value.contains(char::is_whitespace) {
            return None;
        }
        let list = choices(comment?)?;
        let start = name_end + line[name_end..].find(value)?;
        return Some((
            Opt {
                name: name.to_owned(),
                kind: Kind::Choice {
                    current: value.to_owned(),
                    choices: list,
                },
            },
            Edit::Value {
                start,
                end: start + value.len(),
            },
        ));
    }
    if !commented {
        if let Some(rest) = body.strip_prefix("const ") {
            let eq = rest.find('=')?;
            let name = rest[..eq].split_whitespace().last()?;
            if !name.chars().all(is_ident) {
                return None;
            }
            let after = &rest[eq + 1..];
            let semi = after.find(';')?;
            let value = after[..semi].trim();
            let comment = after[semi + 1..].trim_start().strip_prefix("//")?;
            if value.is_empty() || value.contains(char::is_whitespace) {
                return None;
            }
            let list = choices(comment)?;
            let eq_at = line.find('=')?;
            let start = eq_at + 1 + line[eq_at + 1..].find(value)?;
            return Some((
                Opt {
                    name: name.to_owned(),
                    kind: Kind::Choice {
                        current: value.to_owned(),
                        choices: list,
                    },
                },
                Edit::Value {
                    start,
                    end: start + value.len(),
                },
            ));
        }
    }
    None
}

/// The options the texts declare, the first declaration of a name winning.
pub(super) fn discover<'a>(texts: impl Iterator<Item = &'a str>) -> Vec<Opt> {
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for text in texts {
        for line in text.lines() {
            if let Some((opt, _)) = scan(line) {
                if seen.insert(opt.name.clone()) {
                    out.push(opt);
                }
            }
        }
    }
    out
}

/// `line` with the option `name` set to `value` (`on`/`off` for a switch), or `None` when the line does
/// not declare that option or already has the value.
pub(super) fn edit_line(line: &str, name: &str, value: &str) -> Option<String> {
    let (opt, edit) = scan(line)?;
    if opt.name != name {
        return None;
    }
    match (edit, &opt.kind) {
        (
            Edit::Switch {
                commented,
                at,
                hash,
            },
            Kind::Switch(_),
        ) => match (commented, value == "on") {
            (true, true) => Some(format!("{}{}", &line[..at], &line[hash..])),
            (false, false) => Some(format!("{}//{}", &line[..at], &line[at..])),
            _ => None,
        },
        (Edit::Value { start, end }, Kind::Choice { current, .. }) if current != value => {
            Some(format!("{}{}{}", &line[..start], value, &line[end..]))
        }
        _ => None,
    }
}

/// `text` with every choice of `overrides` written into the lines that declare it.
pub(super) fn apply(text: &str, overrides: &[(String, String)]) -> String {
    let wanted: Vec<&(String, String)> = overrides
        .iter()
        .filter(|(name, _)| text.contains(name.as_str()))
        .collect();
    if wanted.is_empty() {
        return text.to_owned();
    }
    let mut out = String::with_capacity(text.len());
    for (i, line) in text.split('\n').enumerate() {
        if i > 0 {
            out.push('\n');
        }
        let mut current = line.to_owned();
        for (name, value) in &wanted {
            if current.contains(name.as_str()) {
                if let Some(edited) = edit_line(&current, name, value) {
                    current = edited;
                }
            }
        }
        out.push_str(&current);
    }
    out
}

/// One word of a `screen` list.
#[derive(Clone, Debug, PartialEq, Eq)]
enum Item {
    Opt(String),
    /// `[NAME]`: another screen.
    Sub(String),
    /// `*`: the options no screen names.
    Rest,
}

/// The screens of shaders.properties: `screen` is the first (its name is empty), `screen.NAME` the others.
#[derive(Clone, Debug, Default)]
pub(super) struct Screens {
    list: Vec<(String, Vec<Item>)>,
}

/// The group of every water option. Not a name a pack can give a screen.
pub(super) const WATER: &str = "\u{1}water";

/// What a page of the settings shows.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) enum Entry {
    /// An option, by its place in the options list.
    Opt(usize),
    /// A screen inside this one, with the number of options under it.
    Sub { name: String, count: usize },
}

impl Screens {
    pub(super) fn parse<'a>(entries: impl Iterator<Item = (&'a str, &'a str)>) -> Self {
        let mut list: Vec<(String, Vec<Item>)> = Vec::new();
        for (key, value) in entries {
            let name = match key.strip_prefix("screen") {
                Some("") => "",
                Some(rest) if rest.starts_with('.') && !rest.ends_with(".columns") => &rest[1..],
                _ => continue,
            };
            if list.iter().any(|(n, _)| n == name) {
                continue;
            }
            let items = value
                .split_whitespace()
                .filter_map(|word| {
                    if let Some(inner) = word.strip_prefix('[').and_then(|w| w.strip_suffix(']')) {
                        Some(Item::Sub(inner.to_owned()))
                    } else if word.starts_with('<') {
                        None
                    } else if word == "*" {
                        Some(Item::Rest)
                    } else {
                        Some(Item::Opt(word.to_owned()))
                    }
                })
                .collect();
            list.push((name.to_owned(), items));
        }
        Screens { list }
    }

    pub(super) fn is_empty(&self) -> bool {
        self.list.is_empty()
    }

    fn get(&self, name: &str) -> Option<&[Item]> {
        self.list
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, items)| items.as_slice())
    }

    /// Every option name some screen lists.
    fn named(&self) -> HashSet<&str> {
        self.list
            .iter()
            .flat_map(|(_, items)| items)
            .filter_map(|item| match item {
                Item::Opt(name) => Some(name.as_str()),
                _ => None,
            })
            .collect()
    }

    /// The entries of `items`, the options of `opts` they name and the screens they open.
    fn resolve(&self, items: &[Item], opts: &[Opt]) -> Vec<Entry> {
        let named = self.named();
        let mut out = Vec::new();
        for item in items {
            match item {
                Item::Opt(name) => {
                    if let Some(i) = opts.iter().position(|o| &o.name == name) {
                        out.push(Entry::Opt(i));
                    }
                }
                Item::Sub(name) => {
                    let count = self.count(name, opts, &mut HashSet::new());
                    if count > 0 {
                        out.push(Entry::Sub {
                            name: name.clone(),
                            count,
                        });
                    }
                }
                Item::Rest => {
                    for (i, o) in opts.iter().enumerate() {
                        if !named.contains(o.name.as_str()) {
                            out.push(Entry::Opt(i));
                        }
                    }
                }
            }
        }
        out
    }

    /// The options under screen `name`, and under the screens inside it.
    fn count(&self, name: &str, opts: &[Opt], seen: &mut HashSet<String>) -> usize {
        if !seen.insert(name.to_owned()) {
            return 0;
        }
        let Some(items) = self.get(name) else {
            return 0;
        };
        let mut total = 0;
        for item in items {
            total += match item {
                Item::Opt(n) => usize::from(opts.iter().any(|o| &o.name == n)),
                Item::Sub(n) => self.count(n, opts, seen),
                Item::Rest => {
                    let named = self.named();
                    opts.iter()
                        .filter(|o| !named.contains(o.name.as_str()))
                        .count()
                }
            };
        }
        total
    }
}

fn is_water(name: &str) -> bool {
    name.to_ascii_uppercase().contains("WATER")
}

/// What the page at `path` (the screens opened so far; empty for the first page) shows: the water group
/// first on the first page, then the pack's own screens in its order. A pack that defines no screens
/// shows all its options on the first page.
pub(super) fn entries(screens: &Screens, opts: &[Opt], path: &[String]) -> Vec<Entry> {
    let named = screens.named();
    let on_screen = |o: &Opt| screens.is_empty() || named.contains(o.name.as_str());
    match path.last().map(String::as_str) {
        Some(WATER) => opts
            .iter()
            .enumerate()
            .filter(|(_, o)| is_water(&o.name) && on_screen(o))
            .map(|(i, _)| Entry::Opt(i))
            .collect(),
        Some(name) => screens
            .get(name)
            .map(|items| screens.resolve(items, opts))
            .unwrap_or_default(),
        None => {
            let mut out = Vec::new();
            let water = opts
                .iter()
                .filter(|o| is_water(&o.name) && on_screen(o))
                .count();
            if water > 0 {
                out.push(Entry::Sub {
                    name: WATER.to_owned(),
                    count: water,
                });
            }
            match screens.get("") {
                Some(items) => out.extend(screens.resolve(items, opts)),
                None if screens.is_empty() => out.extend((0..opts.len()).map(Entry::Opt)),
                None => {
                    for (name, _) in &screens.list {
                        let count = screens.count(name, opts, &mut HashSet::new());
                        if count > 0 {
                            out.push(Entry::Sub {
                                name: name.clone(),
                                count,
                            });
                        }
                    }
                }
            }
            out
        }
    }
}

/// The value a click gives: the next of a list (the first when the current one is not in it), or the
/// other state of a switch.
pub(super) fn next(opt: &Opt) -> String {
    match &opt.kind {
        Kind::Switch(on) => (if *on { "off" } else { "on" }).to_owned(),
        Kind::Choice { current, choices } => {
            let at = choices.iter().position(|c| c == current);
            choices[at.map_or(0, |i| (i + 1) % choices.len())].clone()
        }
    }
}

/// `opt` as it is once set to `value`.
pub(super) fn with_value(opt: &Opt, value: &str) -> Opt {
    let kind = match &opt.kind {
        Kind::Switch(_) => Kind::Switch(value == "on"),
        Kind::Choice { choices, .. } => Kind::Choice {
            current: value.to_owned(),
            choices: choices.clone(),
        },
    };
    Opt {
        name: opt.name.clone(),
        kind,
    }
}

/// The labels of a pack's lang file.
#[derive(Default)]
pub(super) struct Lang(HashMap<String, String>);

impl Lang {
    pub(super) fn parse(text: &str) -> Self {
        let mut map = HashMap::new();
        for line in text.lines() {
            let line = line.trim_start_matches('\u{feff}').trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if let Some((key, value)) = line.split_once('=') {
                map.insert(key.trim().to_owned(), plain(value.trim()));
            }
        }
        Lang(map)
    }

    fn option(&self, name: &str) -> Option<&str> {
        self.0
            .get(&format!("option.{name}"))
            .map(String::as_str)
            .filter(|s| !s.is_empty())
    }

    fn value(&self, name: &str, value: &str) -> Option<&str> {
        self.0
            .get(&format!("value.{name}.{value}"))
            .map(String::as_str)
            .filter(|s| !s.is_empty())
    }

    fn screen(&self, name: &str) -> Option<&str> {
        self.0
            .get(&format!("screen.{name}"))
            .map(String::as_str)
            .filter(|s| !s.is_empty())
    }
}

/// A screen's name as the page shows it: the pack's title for it, else its name in words.
pub(super) fn screen_title(name: &str, lang: &Lang) -> String {
    if name == WATER {
        return "Water".to_owned();
    }
    match lang.screen(name) {
        Some(title) => title.to_owned(),
        None => name.replace('_', " "),
    }
}

/// The button that opens a screen: `Lighting (12) >`.
pub(super) fn sub_label(name: &str, count: usize, lang: &Lang) -> String {
    let tail = format!(" ({count}) >");
    let room = WIDTH.saturating_sub(tail.chars().count());
    let title = screen_title(name, lang);
    if title.chars().count() <= room {
        return format!("{title}{tail}");
    }
    let cut: String = title.chars().take(room.saturating_sub(1)).collect();
    format!("{cut}\u{2026}{tail}")
}

/// Where the player is: `Water`, `Lighting > Shadows`.
pub(super) fn crumbs(path: &[String], lang: &Lang) -> String {
    path.iter()
        .map(|name| screen_title(name, lang))
        .collect::<Vec<_>>()
        .join(" > ")
}

/// A label without Minecraft's `§x` formatting codes.
fn plain(text: &str) -> String {
    let mut out = String::new();
    let mut chars = text.chars();
    while let Some(c) = chars.next() {
        if c == '\u{a7}' {
            chars.next();
        } else {
            out.push(c);
        }
    }
    out
}

const WIDTH: usize = 46;

/// A button's text: `Label (NAME): value`, kept within a button's width.
pub(super) fn label(opt: &Opt, lang: &Lang) -> String {
    let title = lang.option(&opt.name).unwrap_or(&opt.name);
    let head = if title == opt.name {
        opt.name.clone()
    } else {
        format!("{title} ({})", opt.name)
    };
    let value = match &opt.kind {
        Kind::Switch(on) => (if *on { "ON" } else { "OFF" }).to_owned(),
        Kind::Choice { current, .. } => match lang.value(&opt.name, current) {
            Some(text) => format!("{text} ({current})"),
            None => current.clone(),
        },
    };
    let tail = format!(": {value}");
    let room = WIDTH.saturating_sub(tail.chars().count());
    // The label with the option's name when it fits, else the label alone, else the label cut short.
    for text in [head.as_str(), title] {
        if text.chars().count() <= room {
            return format!("{text}{tail}");
        }
    }
    let cut: String = title.chars().take(room.saturating_sub(1)).collect();
    format!("{cut}\u{2026}{tail}")
}

/// `NAME=value` lines.
pub(super) fn parse_overrides(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter_map(|l| l.trim().split_once('='))
        .map(|(n, v)| (n.trim().to_owned(), v.trim().to_owned()))
        .filter(|(n, v)| !n.is_empty() && !v.is_empty())
        .collect()
}

pub(super) fn write_overrides(list: &[(String, String)]) -> String {
    list.iter().map(|(n, v)| format!("{n}={v}\n")).collect()
}

/// `list` with `name` set to `value`.
pub(super) fn set_override(list: &mut Vec<(String, String)>, name: &str, value: &str) {
    match list.iter_mut().find(|(n, _)| n == name) {
        Some(entry) => entry.1 = value.to_owned(),
        None => list.push((name.to_owned(), value.to_owned())),
    }
}

/// The file of a pack's choices: its name, without anything that could leave the folder.
pub(super) fn file_of(pack: &str) -> String {
    let safe: String = pack
        .chars()
        .map(|c| {
            if matches!(c, '/' | '\\' | ':') {
                '_'
            } else {
                c
            }
        })
        .collect();
    format!("{DIR}/{safe}.txt")
}
