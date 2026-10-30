// Runs on what `pack_inputs` returns: terrain gets an alpha cut unless it already has one.
fn cutout(mut inputs: horizon_shaders::GameInputs) -> horizon_shaders::GameInputs {
    if let Some(terrain) = inputs.terrain.as_mut()
        && terrain.alpha_test.is_none()
    {
        terrain.alpha_test = Some(horizon_shaders::AlphaTest {
            op: ">",
            reference: 0.1,
        });
    }
    inputs
}
