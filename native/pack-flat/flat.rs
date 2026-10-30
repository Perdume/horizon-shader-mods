// Whether flat section draws run: wherever the device can (`supported`), unless the player has set
// HORIZON_FLAT_DRAWS, which the original function reads.
pub(crate) fn wanted(
    adapter: &wgpu::Adapter,
    device: &wgpu::Device,
) -> std::ops::ControlFlow<bool> {
    if std::env::var_os("HORIZON_FLAT_DRAWS").is_some() {
        return std::ops::ControlFlow::Continue(());
    }
    std::ops::ControlFlow::Break(super::supported(adapter, device))
}
