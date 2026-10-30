// flat::wanted is asked once, when the renderer is made.
fn ask_flat(adapter: &wgpu::Adapter, device: &wgpu::Device) -> std::ops::ControlFlow<bool> {
    crate::gfx::flat::pack_flat::wanted(adapter, device)
}
