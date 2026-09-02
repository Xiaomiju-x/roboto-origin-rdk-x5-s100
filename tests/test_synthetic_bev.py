import numpy as np

from roboto_upgrade.synthetic_bev import generate_dataset


def test_dataset_is_deterministic_and_well_shaped() -> None:
    first = generate_dataset(4, seed=7, grid_size=24)
    second = generate_dataset(4, seed=7, grid_size=24)

    assert first.history.shape == (4, 4, 24, 24)
    assert first.future.shape == (4, 3, 24, 24)
    assert first.flow.shape == (4, 2, 24, 24)
    assert first.dynamic_mask.shape == (4, 1, 24, 24)
    assert first.uncertainty.shape == (4, 1, 24, 24)
    for left, right in zip(first.__dict__.values(), second.__dict__.values(), strict=True):
        np.testing.assert_array_equal(left, right)


def test_noise_can_be_disabled() -> None:
    batch = generate_dataset(2, seed=11, grid_size=24, sensor_noise=False)
    assert set(np.unique(batch.history)).issubset({0.0, 1.0})
    assert set(np.unique(batch.future)).issubset({0.0, 1.0})
    assert np.isfinite(batch.flow).all()
