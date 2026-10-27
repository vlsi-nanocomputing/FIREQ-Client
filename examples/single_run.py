"""Connect, reset, run a YAML experiment, print the summary and plot it."""

from FIREQ_CLIENT import FireqClient
from FIREQ_PLOTTER import plot_2d

with FireqClient("0.0.0.0", 5000, output_dir="experiment_output") as fq:
    fq.reset_all()
    result = fq.run("yaml_experiment_configurations_examples/Rabi.yaml", preprocess={"gain_r": 0.25})
    result.raise_for_status()

print(f"{result.points_completed}/{result.points_expected} points saved in {result.directory}")
plot_2d(result, quantity="magnitude")
