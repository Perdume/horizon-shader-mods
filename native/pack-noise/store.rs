// Runs when a pack is loaded for drawing, before it is read. Copies the image the pack names with
// `texture.noise` to the cache for the renderer, or removes the copy of the pack before when this
// one names none.
fn store_noise(pack: &crate::Pack) {
    let cache = std::path::Path::new(".horizon-cache");
    let file = cache.join("hz-noise.png");
    let named = pack.read_text("shaders.properties").and_then(|text| {
        text.lines().find_map(|line| {
            let rest = line.trim().strip_prefix("texture.noise")?;
            let value = rest.trim_start().strip_prefix('=')?;
            Some(value.trim().to_owned())
        })
    });
    match named.and_then(|path| pack.read(path.trim_start_matches('/'))) {
        Some(bytes) => {
            let _ = std::fs::create_dir_all(cache);
            let _ = std::fs::write(&file, bytes);
        }
        None => {
            let _ = std::fs::remove_file(&file);
        }
    }
}
