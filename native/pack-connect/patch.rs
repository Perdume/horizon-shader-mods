// Packs written for Iris and OptiFine, as Horizon's loader reads them. Every file of a pack comes
// through `Pack::read_text`; two of the things such a pack expects are put right as it is read, so
// the pack in `shaderpacks/` stays the original download (tools/shader_convert/hz_connect.py does the
// same to a copy, for builds without this mod).
//
// - `shaders.properties` gets the uniforms Iris documents and Horizon does not provide yet, as custom
//   uniforms (Horizon binds those to every program): they were zero ("zero for now" in the log).
// - A function that turns a depth into a view position with only the diagonal of
//   `gbufferProjectionInverse` is rewritten to multiply the whole matrix. Horizon puts view bobbing
//   in `gbufferProjection` (Iris: in `gbufferModelView`), and the diagonal leaves out the bob's
//   rotation: everything lit from that position, shadows first, sways with every step.
//
// A support module's source takes no file attributes, so these are plain comments, not `//!`.

use std::sync::Mutex;
use std::time::SystemTime;

use super::Pack;
use super::pack_connect_options as options;

const SOURCE_SUFFIXES: [&str; 7] = [".fsh", ".vsh", ".gsh", ".csh", ".glsl", ".inc", ".h"];

/// (name, type, expression in shaders.properties' language; a bool is 1 or 0). Values follow Iris's
/// uniform documentation (shaders.properties/current/reference/uniforms/). A custom vec3 is written
/// as the type a pack declares, so `cameraPositionInt` (an ivec3) gets whole numbers; `floor` keeps
/// the Int and the Fract (0 to 1) adding up to the position.
const UNIFORMS: [(&str, &str, &str); 12] = [
    ("bedrockLevel", "int", "-64"),
    ("cloudHeight", "float", "192.33"),
    (
        "cameraPositionFract",
        "vec3",
        "vec3(frac(cameraPosition.x), frac(cameraPosition.y), frac(cameraPosition.z))",
    ),
    (
        "cameraPositionInt",
        "vec3",
        "vec3(floor(cameraPosition.x), floor(cameraPosition.y), floor(cameraPosition.z))",
    ),
    (
        "previousCameraPositionFract",
        "vec3",
        "vec3(frac(previousCameraPosition.x), frac(previousCameraPosition.y), frac(previousCameraPosition.z))",
    ),
    (
        "previousCameraPositionInt",
        "vec3",
        "vec3(floor(previousCameraPosition.x), floor(previousCameraPosition.y), floor(previousCameraPosition.z))",
    ),
    ("isRightHanded", "bool", "1"),
    ("relativeEyePosition", "vec3", "vec3(0, 0, 0)"),
    // The head model is the camera in first person (third person is not tracked).
    (
        "eyePosition",
        "vec3",
        "vec3(cameraPosition.x, cameraPosition.y, cameraPosition.z)",
    ),
    // The world direction the view looks: minus the view's third axis (column 2) in the world.
    (
        "playerLookVector",
        "vec3",
        "vec3(-gbufferModelViewInverse.0.2, -gbufferModelViewInverse.1.2, -gbufferModelViewInverse.2.2)",
    ),
    ("endFlashIntensity", "float", "0"),
    ("endFlashPosition", "vec3", "vec3(0, 0, 0)"),
];

const NOTE: &str =
    "// pack_connect: the whole inverse projection (Horizon puts view bobbing in the projection)";

/// `Pack::read_text` for the files this mod changes; `None` leaves every other file to the original.
pub(super) fn read_text(pack: &Pack, path: &str) -> Option<String> {
    let properties = path == "shaders.properties";
    if !properties && !SOURCE_SUFFIXES.iter().any(|s| path.ends_with(s)) {
        return None;
    }
    let text = match pack.read(path) {
        Some(bytes) => {
            let text = String::from_utf8_lossy(&bytes);
            text.strip_prefix('\u{feff}').unwrap_or(&text).to_owned()
        }
        // A pack without a shaders.properties still gets the uniforms.
        None if properties => String::new(),
        None => return None,
    };
    Some(if properties {
        add_uniforms(text)
    } else {
        apply_options(pack.name(), fix_unprojection(&text))
    })
}

/// The choices a player made for `pack` in its Shader Options page (pack_options): `NAME=value` lines
/// of `shader-options/<pack>.txt`. Read again when the file's time changes, so a click shows at the
/// next load of the pack.
fn choices(pack: &str) -> Vec<(String, String)> {
    static CACHE: Mutex<Option<(String, Option<SystemTime>, Vec<(String, String)>)>> =
        Mutex::new(None);
    let file = options::file_of(pack);
    let stamp = std::fs::metadata(&file).and_then(|m| m.modified()).ok();
    let mut cache = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    if let Some((name, at, list)) = &*cache {
        if name == pack && *at == stamp {
            return list.clone();
        }
    }
    let list = std::fs::read_to_string(&file)
        .map(|text| options::parse_overrides(&text))
        .unwrap_or_default();
    *cache = Some((pack.to_owned(), stamp, list.clone()));
    list
}

/// `text` of a source file of `pack` with the player's choices written into the options it declares.
fn apply_options(pack: &str, text: String) -> String {
    let list = choices(pack);
    if list.is_empty() {
        text
    } else {
        options::apply(&text, &list)
    }
}

/// `text` with the uniforms above that it does not define itself, as lines at its end.
fn add_uniforms(mut text: String) -> String {
    let mut lines = String::new();
    for (name, ty, expr) in UNIFORMS {
        if !defines(&text, name) {
            lines.push_str(&format!("uniform.{ty}.{name}={expr}\n"));
        }
    }
    if !lines.is_empty() {
        if !text.is_empty() && !text.ends_with('\n') {
            text.push('\n');
        }
        text.push_str("# added by pack_connect (a Horizon native mod)\n");
        text.push_str(&lines);
    }
    text
}

/// Whether a line is `uniform.<type>.<name>` or `variable.<type>.<name>`, then `=` or `:`.
fn defines(text: &str, name: &str) -> bool {
    text.lines().any(|line| {
        let line = line.trim_start();
        let Some(rest) = line
            .strip_prefix("uniform.")
            .or_else(|| line.strip_prefix("variable."))
        else {
            return false;
        };
        let Some((_ty, rest)) = rest.split_once('.') else {
            return false;
        };
        let end = rest
            .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
            .unwrap_or(rest.len());
        rest[..end] == *name && matches!(rest[end..].trim_start().chars().next(), Some('=' | ':'))
    })
}

fn is_ident(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_'
}

/// `text` with every `vec3 f(vec3 p) { ... }` that unprojects with the diagonal of
/// `gbufferProjectionInverse` rewritten to multiply the whole matrix. A function counts when its body
/// uses `gbufferProjectionInverse[0].x`, turns its argument into NDC with `* 2.0 - 1.0` and returns
/// `.xyz / .w`; any other shape is left alone.
fn fix_unprojection(text: &str) -> String {
    if !text.contains("gbufferProjectionInverse") {
        return text.to_owned();
    }
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut copied = 0;
    let mut search = 0;
    while let Some(found) = text[search..].find("vec3") {
        let start = search + found;
        search = start + 4;
        if start > 0 && is_ident(bytes[start - 1]) {
            continue;
        }
        let Some((arg, open)) = signature(text, start) else {
            continue;
        };
        let Some(close) = closing_brace(text, open) else {
            continue;
        };
        if !diagonal_unprojection(&text[open + 1..close]) {
            continue;
        }
        out.push_str(&text[copied..=open]);
        out.push_str(&format!(
            "\n    {NOTE}\n    vec4 viewPos = gbufferProjectionInverse * vec4({arg} * 2.0 - 1.0, 1.0);\n    return viewPos.xyz / viewPos.w;\n"
        ));
        copied = close;
        search = close;
    }
    out.push_str(&text[copied..]);
    out
}

/// `vec3 name(vec3 arg) {` at `start`: the argument's name and where the `{` is.
fn signature(text: &str, start: usize) -> Option<(&str, usize)> {
    let mut c = Cursor { text, at: start };
    c.eat("vec3")?;
    c.space1()?;
    c.word()?;
    c.space();
    c.eat("(")?;
    c.space();
    c.eat("vec3")?;
    c.space1()?;
    let arg = c.word()?;
    c.space();
    c.eat(")")?;
    c.space();
    (c.text[c.at..].starts_with('{')).then_some((arg, c.at))
}

struct Cursor<'a> {
    text: &'a str,
    at: usize,
}

impl<'a> Cursor<'a> {
    fn eat(&mut self, s: &str) -> Option<()> {
        self.text[self.at..]
            .starts_with(s)
            .then(|| self.at += s.len())
    }
    fn space(&mut self) -> usize {
        let n = self.text[self.at..]
            .bytes()
            .take_while(u8::is_ascii_whitespace)
            .count();
        self.at += n;
        n
    }
    fn space1(&mut self) -> Option<()> {
        (self.space() > 0).then_some(())
    }
    fn word(&mut self) -> Option<&'a str> {
        let n = self.text[self.at..]
            .bytes()
            .take_while(|b| is_ident(*b))
            .count();
        let word = &self.text[self.at..self.at + n];
        self.at += n;
        (n > 0).then_some(word)
    }
}

/// The index of the `}` that closes the `{` at `open`.
fn closing_brace(text: &str, open: usize) -> Option<usize> {
    let mut depth = 0usize;
    for (i, b) in text.bytes().enumerate().skip(open) {
        match b {
            b'{' => depth += 1,
            b'}' => {
                depth -= 1;
                if depth == 0 {
                    return Some(i);
                }
            }
            _ => {}
        }
    }
    None
}

fn diagonal_unprojection(body: &str) -> bool {
    let code: String = strip_comments(body)
        .chars()
        .filter(|c| !c.is_whitespace())
        .collect();
    code.contains("gbufferProjectionInverse[0].x")
        && ["*2.0-1.0", "*2-1", "*2.-1.", "*2-1.0", "*2.0-1"]
            .iter()
            .any(|p| code.contains(p))
        && code.match_indices(".xyz/").any(|(i, _)| {
            let rest = &code[i + 5..];
            let n = rest.bytes().take_while(|b| is_ident(*b)).count();
            n > 0 && rest[n..].starts_with(".w")
        })
}

fn strip_comments(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(i) = rest.find('/') {
        out.push_str(&rest[..i]);
        let after = &rest[i..];
        if after.starts_with("//") {
            rest = after.find('\n').map_or("", |n| &after[n..]);
        } else if after.starts_with("/*") {
            rest = after[2..].find("*/").map_or("", |n| &after[n + 4..]);
        } else {
            out.push('/');
            rest = &after[1..];
        }
    }
    out.push_str(rest);
    out
}
