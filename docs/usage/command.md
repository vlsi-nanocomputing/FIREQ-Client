# Interactive Commands Reference

The interactive shell provides a direct interface to control the FIREQ server, configure hardware parameters, and manage experiment workflows. Once connected, you can execute the following commands:

---

## Session & Diagnostics

| Command | Description |
| :--- | :--- |
| `ping` | Sends a ping request and prints the server response. |
| `quit`, `exit` | Gracefully closes the active interactive session and disconnects from the server. |

---

## Experiment & Configuration

| Command | Arguments | Description |
| :--- | :--- | :--- |
| `run_yaml` | `<file.yaml>` | Loads the specified YAML configuration, compiles sweep variables, and initiates full experiment execution. |
| `export` | `<source_dir> <target_dir>` | Combines an experiment directory, or a tree of experiment directories, into exported data containing a single `data.pkl` file per experiment. |
| `reset_all` | — | Resets server-side memory buffers, clears IP registers, and restores the system tree to its default state. |

The `export` command is run in the client prompt after an experiment has
finished. For example:

```text
export experiment_output/my_experiment/experiment_<timestamp>/ exported/my_experiment/
```

For swept experiments, the command combines the indexed `data_*.pkl` files into
`data.pkl` and copies `config.json` and `end_message.json` when available. The
resulting directory is the input expected by the plotter.

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
| `trigger_manually` | — | Sends a manual generator trigger request for `/axisGeneratorIP_0`. |