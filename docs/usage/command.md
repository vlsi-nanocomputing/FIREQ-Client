# Interactive Commands Reference

The interactive shell provides a direct interface to control the FIREQ server, configure hardware parameters, and manage experiment workflows. Once connected, you can execute the following commands:

---

## Session & Diagnostics

| Command | Description |
| :--- | :--- |
| `ping` | Sends a ping request and prints the server response. |
| `quit`, `exit` | Gracefully closes the active interactive session and disconnects from the server. `Ctrl-C` at the prompt takes the same path. |

---

## Experiment & Configuration

| Command | Arguments | Description |
| :--- | :--- | :--- |
| `run_yaml` | `<file.yaml>` | Loads the specified YAML configuration, compiles sweep variables, and initiates full experiment execution. |
| `config_yaml` | `<file.yaml>` | Loads the specified YAML configuration and applies it to the server without running the experiment. |
| `export` | `<source_dir> <target_dir>` | Combines an experiment directory, or a tree of experiment directories, into exported data holding a `data_<acquisition_ip_name>.pkl` and `data_<acquisition_ip_name>.csv` pair per acquisition IP. |
| `reset_all` | — | Resets server-side memory buffers, clears IP registers, and restores the system tree to its default state. |

The `export` command is run in the client prompt after an experiment has
finished. For example:

```text
export experiment_output/my_experiment/experiment_<timestamp>/ exported/my_experiment/
```

Every acquisition IP of the experiment is written as one `data_<acquisition_ip_name>.pkl`
pickle, built from that IP's per-sweep-point `data_*.pkl` files combined into a single
dataframe indexed by the sweep variables, together with a matching `.csv` file. The
experiment's `experiment_summary.yaml` is copied next to them. The resulting directory is
the input expected by the plotter.

---

## Hardware Calibration & Synchronization

| Command | Arguments | Description |
| :--- | :--- | :--- |
| `mts_sync` | — | Triggers Multi-Tile Synchronization (MTS) across RF-DC converter tiles to align sample clocks. |
| `set_nyquist` | `<tile> <block_id> <zone>` | Configures the Nyquist operating zone for a target converter block.<br>_Example:_ `set_nyquist 228 0 2` sets the 2nd Nyquist zone on tile 228, block 0. |

---

## Hardware Trigger

| Command | Status | Description |
| :--- | :--- | :--- |
| `trigger_manually` | `<generator_IP_name>` | Sends a manual trigger request for the given generator IP and prints the server response. |