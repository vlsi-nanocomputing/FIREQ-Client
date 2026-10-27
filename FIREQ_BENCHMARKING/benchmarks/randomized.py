"""Single-qubit randomized benchmarking flow for FIREQ."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from FIREQ_BENCHMARKING.utils.yaml_generator import YAMLGenerator
from FIREQ_CLIENT.client import Client
from FIREQ_CLIENT.export import export


# Server configuration
HOST = "0.0.0.0"
PORT = 5000

# Benchmark configuration
SHOTS = 1000
SEED = 1
CLIFFORD_LENGTHS = (1, 2, 4, 8, 16, 32, 64)
SEQUENCES_PER_LENGTH = 10

# Output directories
BENCHMARK_DIR = Path(__file__).resolve().parents[2]
GENERATED_DIR = BENCHMARK_DIR / "generated"
EXPORT_DIR = BENCHMARK_DIR / "exported"
RESULTS_DIR = BENCHMARK_DIR / "results"

# Qubit and setup parameters
QUBIT_PARAMETERS = {
    "drive_modulation_frequency": 5625.50,
    "readout_modulation_frequency": 7583.46,
    "readout_duration": 1000,
    "gain_r": 0.2,
    "tof": 200,
    "drive_duration": 200,
}

# Rabi-specific parameters
RABI_PARAMETERS = {
    "shots": SHOTS,
    "gain_sweep_start": 0.05,
    "gain_sweep_stop": 0.3,
    "gain_sweep_num": 1000,
    "gain_sweep_mode": "lin",
}

# Randomized benchmarking timing
DRIVE_DELAY = 100
READOUT_MARGIN = 100
EXPERIMENT_MARGIN = 1000


I = np.eye(2, dtype=complex)
X = np.array([[0, 1], [1, 0]], dtype=complex)
Y = np.array([[0, -1j], [1j, 0]], dtype=complex)


def rotation(pauli: np.ndarray, angle: float) -> np.ndarray:
    """Return a single-qubit rotation matrix."""
    return np.cos(angle / 2) * I - 1j * np.sin(angle / 2) * pauli


PRIMITIVES = {
    "x90": rotation(X, np.pi / 2),
    "xm90": rotation(X, -np.pi / 2),
    "y90": rotation(Y, np.pi / 2),
    "ym90": rotation(Y, -np.pi / 2),
    "x180": rotation(X, np.pi),
    "y180": rotation(Y, np.pi),
}


def unitary_key(unitary: np.ndarray) -> tuple[float, ...]:
    """Return a global-phase-independent unitary key."""
    value = next(value for value in unitary.flat if abs(value) > 1e-12)
    normalized = unitary * np.exp(-1j * np.angle(value))
    values = np.concatenate((normalized.real.ravel(), normalized.imag.ravel()))
    return tuple(np.round(values, 10))


def build_cliffords() -> list[tuple[np.ndarray, list[str]]]:
    """Build the 24 single-qubit Clifford gates from primitive pulses."""
    cliffords = {unitary_key(I): (I, [])}
    pending = [(I, [])]

    while pending:
        unitary, sequence = pending.pop(0)

        for name, primitive in PRIMITIVES.items():
            new_unitary = primitive @ unitary
            key = unitary_key(new_unitary)

            if key not in cliffords:
                cliffords[key] = (new_unitary, sequence + [name])
                pending.append((new_unitary, sequence + [name]))

    return list(cliffords.values())


CLIFFORDS = build_cliffords()
CLIFFORD_LOOKUP = {unitary_key(unitary): sequence for unitary, sequence in CLIFFORDS}


def latest_experiment(client: Client, name: str) -> Path:
    """Return the latest output directory for an experiment."""
    experiment_dir = Path(client.output_dir) / name
    return max(experiment_dir.glob("experiment_*"), key=lambda path: path.stat().st_mtime)


def run_experiment(client: Client, yaml_path: Path) -> Path:
    """Run a generated FIREQ experiment and return its exported directory."""
    client._run_yaml(str(yaml_path))
    return export_experiment(latest_experiment(client, yaml_path.stem), yaml_path.stem)


def export_experiment(experiment_dir: Path, name: str) -> Path:
    """Export a FIREQ experiment."""
    output_dir = EXPORT_DIR / name
    export(experiment_dir, output_dir)
    return output_dir


def load_exported(export_dir: Path) -> pd.DataFrame:
    """Load the data of an exported experiment with a single acquisition source."""
    # shortcut: takes the first source only, upgrade when acquiring from more than one IP
    return pd.read_pickle(next(export_dir.glob("data_*.pkl")))


def calibrate_rabi(experiment_dir: Path) -> float:
    """Estimate the pi-pulse gain from a Rabi experiment."""
    dataframe = load_exported(experiment_dir)

    with open(experiment_dir / "experiment_summary.yaml", encoding="utf-8") as file:
        summary = yaml.unsafe_load(file)

    gains = np.asarray(summary["var_values"]["gain_sweep"], dtype=float)
    iq = dataframe.groupby(level="gain_sweep")["value"].mean().to_numpy()

    # Project the IQ trajectory onto its principal direction
    points = np.column_stack((np.real(iq), np.imag(iq)))
    points -= np.mean(points, axis=0)
    _, _, vh = np.linalg.svd(points, full_matrices=False)
    signal = points @ vh[0]
    signal -= np.mean(signal)

    # Estimate the Rabi frequency
    gain_step = np.mean(np.diff(gains))
    fft_size = len(signal) * 16
    frequencies = np.fft.rfftfreq(fft_size, d=gain_step)
    spectrum = np.abs(np.fft.rfft(signal, n=fft_size))
    frequency_guess = frequencies[np.argmax(spectrum[1:]) + 1]
    best_frequency = frequency_guess
    best_error = np.inf

    # Refine the frequency using a sinusoidal least-squares fit
    for frequency in np.linspace(0.5 * frequency_guess, 1.5 * frequency_guess, 2000):
        phase = 2.0 * np.pi * frequency * gains
        design = np.column_stack((np.cos(phase), np.sin(phase), np.ones(len(gains))))
        coefficients = np.linalg.lstsq(design, signal, rcond=None)[0]
        error = np.mean((signal - design @ coefficients) ** 2)

        if error < best_error:
            best_error = error
            best_frequency = frequency

    return 1.0 / (2.0 * best_frequency)


def generate_clifford_sequence(length: int, rng: np.random.Generator) -> list[str]:
    """Generate a random Clifford sequence followed by its recovery Clifford."""
    total = I.copy()
    drive_order = []

    for index in rng.integers(0, len(CLIFFORDS), size=length):
        unitary, sequence = CLIFFORDS[index]
        total = unitary @ total
        drive_order.extend(sequence)

    recovery = total.conj().T
    drive_order.extend(CLIFFORD_LOOKUP[unitary_key(recovery)])

    return drive_order


def randomized_parameters(drive_order: list[str], gain_pi: float) -> dict:
    """Build the parameters for one randomized benchmarking experiment."""
    pulse_time = len(drive_order) * QUBIT_PARAMETERS["drive_duration"]
    readout_delay = pulse_time + READOUT_MARGIN if drive_order else READOUT_MARGIN
    experiment_duration = DRIVE_DELAY + readout_delay + QUBIT_PARAMETERS["readout_duration"] + EXPERIMENT_MARGIN

    return {
        **QUBIT_PARAMETERS,
        "shots": SHOTS,
        "gain_pi": gain_pi,
        "drive_order": drive_order,
        "drive_delay": DRIVE_DELAY,
        "readout_delay": readout_delay,
        "experiment_duration": experiment_duration,
    }


def run_randomized(client: Client, generator: YAMLGenerator, name: str, drive_order: list[str], gain_pi: float) -> complex:
    """Generate, run, and return the mean IQ value of one randomized experiment."""
    yaml_path = generator.generate(
        experiment="randomized",
        parameters=randomized_parameters(drive_order, gain_pi),
        output_path=GENERATED_DIR / f"{name}.yaml",
    )

    client._reset_all()
    dataframe = load_exported(run_experiment(client, yaml_path))

    return complex(dataframe["value"].mean())


def survival_probability(iq: complex, iq_0: complex, iq_1: complex) -> float:
    """Convert the mean IQ value into the estimated ground-state probability."""
    axis = iq_1 - iq_0
    probability_1 = np.real((iq - iq_0) * np.conj(axis)) / abs(axis) ** 2

    return float(np.clip(1.0 - probability_1, 0.0, 1.0))


def fit_rb(lengths: np.ndarray, survival: np.ndarray) -> tuple[float, float, float]:
    """Fit A * p**m + B and return A, p, and B."""
    best_error = np.inf
    best_fit = (0.0, 0.0, 0.0)

    for p in np.linspace(0.0, 1.0, 5001):
        design = np.column_stack((p**lengths, np.ones(len(lengths))))
        amplitude, offset = np.linalg.lstsq(design, survival, rcond=None)[0]
        error = np.mean((survival - (amplitude * p**lengths + offset)) ** 2)

        if error < best_error:
            best_error = error
            best_fit = (float(amplitude), float(p), float(offset))

    return best_fit


def save_results(
    gain_pi: float,
    iq_0: complex,
    iq_1: complex,
    lengths: list[int],
    means: list[float],
    stds: list[float],
    fit: tuple[float, float, float],
) -> None:
    """Save the randomized benchmarking results."""
    amplitude, decay, offset = fit
    epc = (1.0 - decay) / 2.0

    results = {
        "gain_pi": gain_pi,
        "gain_pi_half": gain_pi / 2,
        "iq_0": {"real": iq_0.real, "imag": iq_0.imag},
        "iq_1": {"real": iq_1.real, "imag": iq_1.imag},
        "lengths": lengths,
        "survival_mean": means,
        "survival_std": stds,
        "fit": {
            "amplitude": amplitude,
            "decay": decay,
            "offset": offset,
        },
        "error_per_clifford": epc,
        "clifford_fidelity": 1.0 - epc,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "randomized.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


def main() -> None:
    """Run the complete single-qubit randomized benchmarking flow."""
    generator = YAMLGenerator()
    rng = np.random.default_rng(SEED)
    client = Client(HOST, PORT)

    try:
        client._connect()
        client._do_handshake()
        client._reset_all()

        # Run the Rabi calibration
        rabi_yaml = generator.generate(
            experiment="rabi",
            parameters={**QUBIT_PARAMETERS, **RABI_PARAMETERS},
            output_path=GENERATED_DIR / "rabi_calibration.yaml",
        )

        gain_pi = calibrate_rabi(run_experiment(client, rabi_yaml))

        print(f"Calibrated pi gain: {gain_pi:.6f}")

        # Acquire readout references for |0> and |1>
        iq_0 = run_randomized(client, generator, "rb_reference_0", [], gain_pi)
        iq_1 = run_randomized(client, generator, "rb_reference_1", ["x180"], gain_pi)

        lengths = []
        means = []
        stds = []

        # Run randomized Clifford sequences
        for length in CLIFFORD_LENGTHS:
            survival = []

            for sequence_index in range(SEQUENCES_PER_LENGTH):
                drive_order = generate_clifford_sequence(length, rng)
                name = f"rb_m{length:04d}_s{sequence_index:03d}"
                iq = run_randomized(client, generator, name, drive_order, gain_pi)
                survival.append(survival_probability(iq, iq_0, iq_1))

            lengths.append(length)
            means.append(float(np.mean(survival)))
            stds.append(float(np.std(survival)))

            print(f"m={length}: P0={means[-1]:.6f} +/- {stds[-1]:.6f}")

        # Fit the RB decay and save the results
        fit = fit_rb(np.asarray(lengths), np.asarray(means))
        save_results(gain_pi, iq_0, iq_1, lengths, means, stds, fit)

        _, decay, _ = fit
        epc = (1.0 - decay) / 2.0

        print(f"RB decay: {decay:.8f}")
        print(f"Error per Clifford: {epc:.8e}")
        print(f"Clifford fidelity: {1.0 - epc:.8f}")

    finally:
        client._disconnect()


if __name__ == "__main__":
    main()
