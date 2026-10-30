// Runs before the renderer makes `noisetex`. When the cache holds the pack's own image (a square
// PNG), that is the texture; otherwise Horizon's white noise is made as before.
fn pack_noise(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
) -> core::ops::ControlFlow<wgpu::TextureView> {
    let Ok(bytes) = std::fs::read(".horizon-cache/hz-noise.png") else {
        return core::ops::ControlFlow::Continue(());
    };
    match crate::hud::decode_bytes(&bytes) {
        Ok((w, h, rgba)) if w == h && w > 0 && rgba.len() == (w as usize) * (h as usize) * 4 => {
            core::ops::ControlFlow::Break(texture(
                device,
                queue,
                "Shader pack noisetex (the pack's image)",
                w,
                &rgba,
            ))
        }
        _ => core::ops::ControlFlow::Continue(()),
    }
}
