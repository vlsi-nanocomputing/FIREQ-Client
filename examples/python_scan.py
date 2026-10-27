"""Scan a preprocess value from Python, one run per value, and plot the runs together."""

from FIREQ_CLIENT import FireqClient
from FIREQ_PLOTTER import plot_2d

FREQUENCIES = [5625.0, 5625.5, 5626.0]

results = []
with FireqClient("0.0.0.0", 5000) as fq:
    for frequency in FREQUENCIES:
        # the v0 server needs a reset before running a configuration again
        result = fq.run(
            "yaml_experiment_configurations_examples/Rabi.yaml",
            name=f"rabi_{frequency}",
            preprocess={"drive_modulation_frequency": frequency},
            reset_before=True,
        )
        result.raise_for_status()
        results.append(result)

plot_2d(results)
