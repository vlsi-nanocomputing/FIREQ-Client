# Interactive Commands Reference

The interactive shell provides a direct interface to control the FIREQ server, configure hardware parameters, and manage experiment workflows. Once connected, you can execute the following commands:

---

## Session & Diagnostics

| Command | Description |
| :--- | :--- |
| `ping` | Verifies connectivity and measures latency between the client and the server. |
| `quit`, `exit` | Gracefully closes the active interactive session and disconnects from the server. |

---

## Experiment & Configuration

| Command | Arguments | Description |
| :--- | :--- | :--- |
| `apply_config` | `<file.yaml>` | Parses and sends a YAML configuration to the server to update node parameters and build DAG dependencies without executing the experiment. |
| `run_yaml` | `<file.yaml>` | Loads the specified YAML configuration, compiles sweep variables, and initiates full experiment execution. |
| `reset_all` | — | Resets server-side memory buffers, clears IP registers, and restores the system tree to its default state. |

---

## Hardware Calibration & Synchronization

| Command | Arguments | Description |
| :--- | :--- | :--- |
| `mts_sync` | — | Triggers Multi-Tile Synchronization (MTS) across RF-DC converter tiles to align sample clocks. |
| `set_nyquist` | `<tile> <block_id> <zone>` | Configures the Nyquist operating zone for a target converter block.<br>_Example:_ `set_nyquist 228 0 2` sets the 2nd Nyquist zone on tile 228, block 0. |

---

## Developer & Planned Commands

| Command | Status | Description |
| :--- | :--- | :--- |
| `trigger manually` | *Placeholder* | Issues an immediate manual hardware trigger request. _(Intended for future release functionality)_ |