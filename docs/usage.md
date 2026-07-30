# Usage

The client is primarily used through an interactive command loop started by:

```bash
python run_client.py
```

## Interactive commands

Once connected, the client accepts the following commands:

- `ping`: checks that the connection to the server is working.
- `apply_config <file.yaml>`: loads a configuration from YAML without immediately executing it.
- `run_yaml <file.yaml>`: loads and executes an experiment described by a YAML file.
- `reset_all`: resets the server-side state.
- `mts_sync`: triggers synchronization across multiple tiles.
- `set_nyquist <tile> <block_id> <zone>`: applies the Nyquist-zone configuration for the selected tile and block. For example, `set_nyquist 228 0 2` applies the second Nyquist zone to tile 228, block 0.
- `trigger manually`: sends a manual trigger request. This is currently marked as a placeholder and is intended for future implementation.
- `quit` or `exit`: closes the interactive session.

## Export workflow

The client also provides an export utility that creates a reduced version of an experiment directory. The command is used as follows:

```bash
export <source_dir> <target_dir>
```

The target folder contains a compact PKL-based representation of the collected experiment data, which is useful for post-processing.

## YAML-based experiments

Experiment definitions are stored in the `experiments/` directory. A typical YAML file contains:

- a `preprocess` section with static values and reusable constants,
- a `variables` section describing parameter sweeps,
- a `sys_config` section with the hardware configuration tree.

The client resolves placeholders such as `%value` using the preprocessing section of the YAML file before sending the configuration to the server.

### Prefix conventions

The YAML parser uses special prefixes to define how values are interpreted:

- `%`: preprocess macro, resolved from the `preprocess` section.
- `#`: sweep expression, used for variables evaluated during a sweep.
- `$`: hardware parameter, mapped to a node callback in the server-side configuration.
- `_`: metadata field used to describe node properties or sub-system attributes.
- `/`: hardware node identifier that targets a specific FPGA block.

### Mutable parameters and state handling

When a parameter is modified through the YAML structure, it remains associated with the same named component in memory for the rest of the session. For example, if a pulse block is defined with a name such as `rect_d`, changing its duration later should be done by referring to that existing named block rather than redefining the full pulse section from scratch.

In practice, this means that the client and server maintain the current hardware state across configuration updates, so users may need to reset the system or update the relevant parameters carefully when changing experiments.

## Example: TOF experiment

A simple example is the TOF experiment configuration stored in `experiments/tof.yaml`. It demonstrates how a sweep over a time-of-flight parameter is defined and how the resulting acquisition data are stored.

A typical configuration includes:

- a preprocess section with frequencies, gain, duration, and shots,
- a `variables` section for the sweep values,
- a `sys_config` section with nodes such as the generator, the acquisition unit, and the trigger generator.

## Running an experiment

A simple workflow is:

1. open the client with `python run_client.py`,
2. connect to the server,
3. run an experiment with `run_yaml experiments/Rabi.yaml`,
4. wait for the acquisition to complete,
5. inspect the generated output files.

## Output structure

Each experiment run creates a timestamped output directory under:

```text
experiment_output/<experiment_name>/experiment_<timestamp>/
```

The folder is created automatically if it does not exist. Inside it, the client stores the experiment under the experiment name and writes the files produced during acquisition.

For a non-swept experiment, the output typically includes:

- `config.json`: the full configuration used for the run,
- `data.csv` and `data.pkl`: the main data outputs,
- `end_message.json`: summary information reported by the server.

For swept experiments, the client writes one data file pair per sweep step. If the experiment contains a single sweep variable with 100 values, the output contains files such as:

- `file0.csv`, `file0.pkl`
- `file1.csv`, `file1.pkl`
- ...
- `file100.csv`, `file100.pkl`

If the experiment contains two sweep variables, such as gain and frequency, the output uses a two-index naming convention such as:

- `file0_0.csv`, `file0_0.pkl`
- `file0_1.csv`, `file0_1.pkl`
- ...
- `file100_100.csv`, `file100_100.pkl`

The numbering reflects the sweep index associated with each variable. The file `var_order.json` records the order of the sweep variables so that each index can be mapped back to the corresponding parameter.

## Plotting and Post-Processing

The repository includes plotting utilities and post-processing tools to inspect and analyze experiment data after acquisition.

The main plotting commands available are:

* `plot_2d`: Plots two-dimensional views of the acquired data.
* `plot_3d_heat`: Creates a three-dimensional or heatmap-style visualization.
* `plot_iq`: Compares two experiment datasets in an IQ-plane representation.
* `plot_spectr`: Generates spectral comparisons from two experiment datasets.

The plotting interface is available through the repository script `plotter.py`, while export utilities can be used to generate a reduced representation of the experiment results.

---

### Command Usage Examples

#### `plot_2d`
Visualizes two-dimensional experimental data with dynamic sweep control:

* **Single-sweep variable**: Generates a standard 2D plot mapping the recorded signal against the swept parameter.
* **Multi-sweep variables**: Automatically detects additional sweep parameters and renders an interactive slider, allowing you to cycle through slices of the higher-dimensional dataset.

#### `plot_3d_heat`
Renders two-dimensional sweep spaces using heatmaps:

* **Two-sweep variables**: Creates a 2D color intensity map (heatmap) mapping two independent swept variables against the acquired readout signal.
* **Time-of-Flight (ToF) visualization**: Can also be used to plot ToF trace evolution over a single sweep variable, provided that the data mode is set to either `decimated` or `raw` (in `accumulated` mode, ToF collapses to a single averaged value).

#### `plot_iq`
Used to evaluate state discrimination on the complex plane for single-shot measurements **without variable sweeps**:

```bash
plot_iq plot_iq_state_0.pkl plot_iq_state_1.pkl
```

* **`plot_iq_state_0.pkl`**: Contains $N$-shot readout data acquired in state $|0\rangle$, without any variable sweeps.
* **`plot_iq_state_1.pkl`**: Contains $N$-shot readout data acquired in state $|1\rangle$, under identical conditions without sweeps.

Outputting a 2D scatter plot in the complex IQ plane.
---

### `plot_spectr`

Similarly, `plot_spectr` compares readout spectroscopy data across a single swept variable (e.g., readout frequency):

```bash 
plot_spectr plot_spectr_state_0.pkl plot_spectr_state_1.pkl
```
* **Prerequisite**: Both files must contain data from experiments executed with the same number of sweep steps over the exact same parameter.
* **Usage**: Overlays the response curves for state $|0\rangle$ and state $|1\rangle$ to visually assess cavity/qubit frequency shifts.