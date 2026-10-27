# Scripting the Client and the Plotter

Everything the prompts do is available from Python. `FIREQ_CLIENT.FireqClient` connects to the
server, runs experiments and returns their results; the `FIREQ_PLOTTER` functions plot directories,
results or DataFrames. The prompts (`python run_client.py`, `python run_plotter.py`) are thin layers
on top of them.

```python
from FIREQ_CLIENT import FireqClient
from FIREQ_PLOTTER import plot_2d

with FireqClient("rfsoc.lab", 5000, token="fireq", output_dir="data") as fq:
    fq.reset_all()
    result = fq.run("Rabi.yaml", preprocess={"gain_r": 0.25})
    result.raise_for_status()
    fq.start_interactive()          # optional: same connection, interactive prompt; returns on `quit`

plot_2d(result, quantity="magnitude", x="gain_sweep")
```

`pip install .` installs the library only (msgpack, numpy, pandas, PyYAML). `pip install ".[all]"`
adds the prompts (`prompt_toolkit`, `tqdm`) and the plotter (`matplotlib`), and the `fireq-client`
and `fireq-plotter` commands. The library prints nothing: it logs to the `FIREQ_CLIENT` and
`FIREQ_PLOTTER` loggers, configure `logging` to see the messages.

## 1. Client

### 1.1 Connection

| Call | Description |
| :--- | :--- |
| `FireqClient(host, port=5000, *, token, client_name, output_dir, connect_timeout=5, command_timeout=30, autoconnect=False)` | Creates the client. `command_timeout=None` waits forever. |
| `connect(verify=True)` | Opens the connection and performs the handshake. With `verify`, sends a ping: the v0 server refuses a wrong token by closing the connection, and this is the only way to report it at connection time. |
| `close()`, `reconnect()` | Close, and close then connect again. |
| `connected` | True while the connection works. |
| `with FireqClient(...) as fq:` | Connects, and closes at the end of the block. |

Only one client can be connected to the v0 server: a second one gets no handshake, and `connect()`
raises `ConnectionFailed` after `connect_timeout`.

### 1.2 Commands

Every method blocks until the complete reply of the server, then returns or raises.

| Method | Returns |
| :--- | :--- |
| `ping()` | the round-trip time, in seconds |
| `reset_all()`, `mts_sync()`, `set_nyquist(tile, block, zone)`, `trigger_manually(ip_name)` | — |
| `apply_configuration(config, *, preprocess=None, variables=None)` | the server warnings |
| `run(config, *, name=None, preprocess=None, variables=None, save=True, keep_in_memory=False, reset_before=False, progress=None, timeout=None)` | an `ExperimentResult` |
| `run_iter(config, ...)` | an iterator over the completed points (`PointResult`) |
| `export(from_dir, to_dir)` | — |

`config` is a YAML path, a dict shaped like the YAML (`preprocess`, `variables`, `sys_config`) or
an `ExperimentConfig` from `load_config`. `preprocess` is merged over the `preprocess:` block before
the `%` substitution, `variables` replaces or extends the `variables:` block. The configuration is
checked before it is sent: a missing `sys_config`, an undefined `%` token, a non-string key or a
value msgpack cannot encode raise `ConfigurationError` right away.

`reset_before=True` sends `reset_all` first: the v0 server needs it to run a configuration again.

`progress` is called once per completed point with a `ProgressEvent(point_index, points_total,
coordinates, iteration_time_ns)`.

### 1.3 Results

`run()` returns an `ExperimentResult` for every run that started, aborted or not:

| Field / method | Description |
| :--- | :--- |
| `name`, `directory` | experiment name, and the `experiment_<timestamp>` directory (None with `save=False`) |
| `variable_order`, `variable_values` | swept variables, outer to inner, and their values |
| `points_expected`, `points_completed`, `aborted` | progress of the run |
| `server_errors`, `warnings`, `runtimes_ns`, `run_time_ns` | what the server reported |
| `sources` | acquisition IPs that produced data |
| `dataframe(source=None)` | the combined data of one IP (optional with a single IP), indexed by the swept variables, `shot` and `time` |
| `dataframes()` | the data of every IP |
| `raise_for_status()` | raises `RunFailed` if the run was aborted or the server reported errors |

The data is read from disk, or from memory with `keep_in_memory=True`.
`FIREQ_CLIENT.load_experiment(directory)` loads a saved directory back as an `ExperimentResult`.

`run_iter()` yields each point as soon as it completes; the result is in the iterator's `result` at
the end. Use it in a `with` block to stop early: leaving the block closes the connection, which is
the only way to stop a sweep with the v0 protocol. Call `reconnect()` to go on.

### 1.4 Errors

```text
FireqError
├── ConnectionFailed        connect() failed (TCP, no handshake, wrong token)
├── ConnectionLost          the connection dropped
├── FireqTimeout            no reply within the timeout; the client is then Desynchronized
├── Desynchronized          a reply may belong to an older command: call reconnect()
├── ProtocolError           unexpected packet (e.g. another server version)
├── ServerError             the server answered with an error packet
│   ├── ConfigurationError  invalid configuration, locally or on the server (send reset_all)
│   └── RunRejected         config_and_run refused before the run started
└── RunFailed               raised by ExperimentResult.raise_for_status()
```

`Ctrl-C` during `run()` closes the connection (the server stops before the next point) and the
`KeyboardInterrupt` carries the partial result as `err.result`.

## 2. Plotter

```python
from FIREQ_PLOTTER import plot_2d, plot_3d_heatmap, plot_iq, rotated_cdf, load_experiment
```

Every plot function accepts an experiment directory (exported, or saved by the client), an
`ExperimentResult`, a DataFrame, a dict of DataFrames by acquisition IP, or a list of those.

| Function | Presets |
| :--- | :--- |
| `plot_2d(data, *, ip, quantity, x, selection, average_over, interactive, show, block)` | `quantity` ∈ `magnitude`, `phase`, `phase_unwrapped`, `real`, `imag`, `real_imag` |
| `plot_3d_heatmap(data, *, ip, quantity, x, y, dataset, selection, ...)` | `quantity` ∈ `magnitude`, `phase`, `real`, `imag` |
| `plot_iq(data, *, ip, curve_parameter, selection, cdf_analysis, ...)` | readout fidelity in `handles.cdf` |
| `rotated_cdf(data_0, data_1, *, ip, plot=True, show, block)` | returns `CdfResult(figure, fidelity)` |

- `selection` presets the sliders, e.g. `{"gain_sweep": 0.2}` (snapped to the closest value).
- `ip=None` plots every IP common to all inputs and returns a list of handles; an IP name returns one.
- `show=False` builds the figures and returns: save them with `handles.figure.savefig(...)`.
- `interactive=False` draws static figures, without widgets (works with the `Agg` backend).
- Keep the returned handles referenced while using the widgets of a non-blocking figure (Jupyter).

## 3. Command line

`python run_client.py` (or `fireq-client`) accepts options, and prompts only for the missing ones:

```bash
fireq-client --host rfsoc.lab --port 5000 --token-env FIREQ_TOKEN --output-dir data --log-level info
fireq-client --host rfsoc.lab --port 5000 --token-env FIREQ_TOKEN --output-dir data --script commands.txt
```

`--script` runs the prompt commands of a file (one per line, `#` comments) and stops at the first
failure.

## 4. Examples

The `examples/` directory holds `single_run.py`, `python_scan.py` (a scan in Python over a
preprocess value), `live_analysis.py` (`run_iter` with an early stop), `headless_report.py` (PNG
files without a display) and `script_then_repl.py`.
