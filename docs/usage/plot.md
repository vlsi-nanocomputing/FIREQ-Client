# Plotting and Post-Processing

## 1. Overview of Visualization Utilities

| Command | Target Data Dimensionality | Input | Primary Use Case |
| :--- | :--- | :--- | :--- |
| `plot_2d` | 1D / Multi-D (with Slicers) | Experiment directory with `data.pkl` | Standard line plots for 1D sweeps or multi-sweep data with dynamic slice controls. |
| `plot_3d_heat` | 2D / Time-Domain Traces | Experiment directory with `data.pkl` | 2D color maps (heatmaps) for 2-variable sweeps or time-of-flight evolution over a sweep. |
| `plot_iq` | Single-Shot IQ Cloud | Two experiment directories with `data.pkl` | Complex plane scatter plots for state discrimination ($\vert{}0\rangle$ vs $\vert{}1\rangle$). |
| `plot_spectr` | 1D Frequency / Power Sweeps | Two experiment directories with `data.pkl` | Overlaid response curves comparing transmission/dispersion shifts between two states. |

---

## 2. Command Reference & Usage

### 2.1 `plot_2d`

Visualizes standard $Y(X)$ response curves across parameter sweeps. The input
directory must contain `data.pkl`. Non-swept experiments can be plotted
directly after `run_yaml`; swept experiments require the client's `export`
command first.

#### Behavior
* **Single Sweep Variable:** Generates a 2D line plot mapping the measured IQ magnitude or quadrature components against the swept parameter.
* **Multi-Sweep Variables:** Automatically detects additional sweep dimensions and inserts interactive UI sliders, allowing real-time navigation through 1D slices of higher-dimensional datasets.

---

### 2.2 `plot_3d_heat`

Renders two-dimensional parameter spaces using color intensity maps (heatmaps).

#### Behavior
* **Two-Variable Sweeps:** Maps two independent variables ($X, Y$) against the acquired signal intensity ($Z$) on a 2D grid.
* **Time-of-Flight (ToF) Evolution:** Visualizes the full time-domain trace over a single sweep parameter.

> **Note on Data Modes:**  
> Time-of-Flight trace visualization requires acquisition data to be recorded in `decimated` or `raw` output mode. In `accumulated` mode, the time-domain trace is integrated on-chip, collapsing the ToF axis into a single scalar value.

---

### 2.3 `plot_iq`

Evaluates single-shot state classification by plotting single-point readout shots on the complex plane ($I$ vs $Q$).

#### Usage Syntax

    python run_plotter.py
    # inside the interactive prompt:
    plot_iq path/to/exported_state_0/ path/to/exported_state_1/

#### Input Requirements & Characteristics

- **State $\vert{}0\rangle$ Directory (`state_0/`):** Path to an experiment folder containing `config.json` and `data.pkl`, acquired with no variables and `$output_type: accumulated` while the system is prepared in state $\vert{}0\rangle$.
- **State $\vert{}1\rangle$ Directory (`state_1/`):** Same as above for state $\vert{}1\rangle$.
- **Output:** A 2D scatter plot on the complex plane illustrating shot distribution, thermal noise spread, and state separation distance.

### 2.4 `plot_spectr`
Compares spectral response curves (e.g., resonator transmission or qubit spectroscopy) between two experiment configurations.

#### Usage Syntax
    python run_plotter.py
    # inside the interactive prompt:
    plot_spectr path/to/exported_state_0/ path/to/exported_state_1/

#### Input Requirements & Characteristics

- **Prerequisite:** Both target directories must contain `data.pkl` files from experiments executed with the exact same sweep vector (same range, step count, and variable target). Export swept experiments before plotting them.
- **Output:** Overlays response curves for state $\vert{}0\rangle$ and state $\vert{}1\rangle$, enabling direct measurement of dispersive frequency shifts ($\chi$) or readout contrast optimization.
