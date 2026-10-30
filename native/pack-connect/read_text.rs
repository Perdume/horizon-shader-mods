// A pack's text files are read here (`Pack::read_text`); the ones pack_connect changes are given back
// changed, every other file goes on to the original body.
fn read_text(pack: &crate::Pack, path: &str) -> std::ops::ControlFlow<Option<String>> {
    match crate::pack::pack_connect::read_text(pack, path) {
        Some(text) => std::ops::ControlFlow::Break(Some(text)),
        None => std::ops::ControlFlow::Continue(()),
    }
}
