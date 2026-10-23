# Changelog

All notable changes to FIREQ-Client are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
tag names follow the `version-<major>.<minor>.<patch>` scheme.

## [Unreleased]

## [0.1.1] - 2026-10-23

Requires FIREQ-Server `v0.1.1`.

### Breaking changes

- **Experiment data model and output layout changed.** A run now writes
  `experiment_summary.yaml` and `runtimes.yaml` at the experiment root, plus one
  subdirectory per acquisition IP (`axisAcquisitionIP_0/`, …) holding
  `data_<sweep indices>.pkl` and `.csv` per acquisition point. `config.json`,
  `var_order.json`, `end_message.json`, `data.pkl`, `configuration.json` and
  `exp_<i>.json` are no longer produced.
- **New server protocol required (server v0.1.1).** The client drives the `status ok` →
  `experiment_header` → `dma_package` → `iteration_ended` → `experiment_footer` flow; an
  older server fails fast with `TypeError: Expected the experiment header from the server`.
- **`export` output changed**: one `data_<acquisition_ip_name>.pkl` + `.csv` per acquisition
  IP, with `experiment_summary.yaml` copied alongside. Experiment directories produced
  before this release contain none of those files and are silently skipped by `export`.
- **Plotter commands changed**: `plot_2d`, `plot_3d_heat` and `plot_iq_curve` now take only
  experiment directories. The mode tokens (`ri`, `r`, `i`, `p`, `f`) and the `save` token
  are gone, `plot_iq` was renamed `plot_iq_curve`, and `plot_spectr` was removed (its use
  case is `plot_2d dir1 dir2`).
- **`trigger_manually <generator_IP_name>`** now requires its argument and uses the message
  format `{"cmd": "trigger_manually", "ip_name": …}`.
- **Preprocessing:** `%name` tokens are resolved anywhere in a value, including inside `#`
  sweep expressions (`"#%var1 - %var2 + 100"` → `"#1000 - 500 + 100"`), and in `variables`
  too. A value that is exactly one token keeps the preprocess value's type, as before. An
  undefined name is an error, also when the file has no `preprocess` block.
- **Library users**: `Client.run()` was renamed `start()`; `Client` takes the optional
  `auth_token`, `output_dir` and `client_name` arguments.

### Added

- `config_yaml <file.yaml>`: applies a YAML configuration to the server without running the
  experiment.
- Interactive plotter with in-plot selectors: quantity (magnitude, phase, phase (unwrapped),
  real, imag, real + imag where meaningful), x and y axes, curve parameter, and a dataset
  selector when several experiments are overlaid; sliders for every remaining swept
  variable; a separate controls window per plot.
- `plot_iq_curve` runs the rotated-CDF fidelity analysis automatically when exactly two
  directories are given.
- CSV written next to every pickle, both for acquisition data and for `export`, so a result
  can be inspected without unpickling it.
- `runtimes.yaml` per run, with the times reported by the server in ns: `runtimes` (one per
  iteration, from `iteration_ended.time`), `run_time` and `aborted` (from
  `experiment_footer`).
- `retry? [y/n]` prompt when the initial connection fails, instead of aborting the client.
- Startup prompts for the authentication token (sent in the handshake) and the output
  directory (default `experiment_output`; its absolute path is logged once connected).
- The client names itself `fireq_client@<hostname>` in the handshake.
- Logging: the network workers log through the client's logger (`FIREQ_CLIENT.client.reader`
  / `.sender`), and connect/disconnect and worker lifecycle messages are log records.
- `mts_sync` and `set_nyquist` read and print the server's reply.
- Tests in `tests/` (`pytest`, in the `dev` extras): network workers, connection loss,
  configuration validation, preprocessing, the server's reply sequences, and the plotter's
  readout fidelity analysis.
- `yaml_experiment_configurations_examples/drive_loopback.yaml` example experiment.
- `FIREQ_PLOTTER.plotting.fidelity`: `compute_readout_fidelity()` returns a
  `ReadoutFidelity` (both fidelities, ε0, ε1, threshold, rotation angle, shot counts),
  without plotting. `_plot_rotated_cdf` now returns the figure and that result.

### Fixed

- A dropped connection no longer hangs the client: waiting commands raise `ConnectionError`,
  the session is closed with a message (no automatic reconnect), and a send error no longer
  kills the sender thread silently.
- Every command reads its reply, so a reply can no longer be taken as the next command's,
  including the `warning` that follows `apply_configuration` when the configuration has sweep
  expressions (detected with the server's rule: the string value of a `$` key starting with
  `#`). Runs rejected by the server are reported, also when the `error` follows `status ok`;
  after an `error` during a run the client reads up to the footer (`aborted: true`, partial
  data kept), and after a client-side failure mid-run it discards the rest of the run up to
  the footer.
- A failing command (bad argument, missing/invalid/empty YAML, server `error`) no longer ends the
  session.
- Ctrl-C and Ctrl-D at the client prompt disconnect gracefully instead of raising a traceback.
- Quitting no longer waits up to 5 s for the socket timeout.
- Windows (no `sendmsg`) send path raised `NameError` on the first message.
- Two runs started in the same second no longer collide: the second one gets
  `experiment_<timestamp>_1`, and so on.
- `trigger_manually` ignored its argument and always triggered `/axisGeneratorIP_0`; it now
  honours the generator name, waits for the server and prints the reply.
- The receive worker could die on an unexpected error (malformed frame, `struct.error`),
  leaving the client hung in a wait with no diagnostic. It now logs, stops the worker and
  reports the lost connection.
- The send worker marked queue items as done even when a send failed, corrupting the queue's
  unfinished count; completion is only recorded after a successful send, and the worker
  tears down on any exception.
- Worker `stop()` no longer closes the socket — the client owns the socket lifetime —
  removing double-close/`EBADF` races during shutdown. Stopping a worker that was never
  started, or disconnecting without a socket, no longer raises.
- Workers joined their own thread when stopping on a dropped connection
  (`RuntimeError: cannot join current thread`).
- A socket read timeout aborted the reader and discarded the original error; timeouts are
  retried and the cause is chained.
- Connection timeout reduced from 60 s to 1 s, so an unreachable server reaches the retry
  prompt immediately; once connected the socket uses a 5 s timeout instead of keeping the
  1 s one. Answering `n` to `retry?` no longer retries.
- `config_yaml` and `run_yaml` reject a YAML without (or with an empty) `sys_config`.
- The expected number of iterations only counts the variables in `variable_order`.
- The progress bar is closed when fetching fails.
- `export` with too few arguments printed a message and silently continued instead of
  returning.
- The auth token typed at startup was ignored.
- The `debug` logging level chosen at startup had no effect on `client.py`.
- Plotter: heatmaps were unusable for three or more swept variables (the pinned index levels
  leaked into the image), data without a `shot` index level raised instead of plotting,
  `plot_iq_curve` limits were unstable under equal aspect, and the CDF analysis is skipped
  gracefully for datasets with fewer than two samples.
- Plotter: the readout fidelity analysis of `plot_iq_curve` ran on shot-averaged data. It
  now uses the un-averaged shots, and only on data with no swept variable other than `shot`
  and one sample per shot (`accumulated` mode); other data is skipped with a message, or
  rejected with `ValueError` when `cdf_analysis=True`.
- Plotter: `plot_iq_curve` on single-run state datasets (`IQ_at_0.yaml` / `IQ_at_1.yaml`, no
  sweep variable) failed with `Not enough axes in data to plot an IQ curve`. The raw shot
  clouds are now plotted, with no selector and no slider, and the fidelity is computed.
- Plotter: the printed "maximum fidelity" was the separation `1 - ε0 - ε1`. The analysis now
  prints the assignment fidelity `1 - (ε0 + ε1)/2`, the separation `1 - ε0 - ε1`, the error
  rates, the threshold and the shot counts.
- Plotter: the threshold was searched on a grid and on the absolute CDF difference. It is
  now the midpoint between shot values that maximises the signed difference `CDF0 - CDF1`,
  and the cumulative distributions are drawn as step functions.

### Changed

- `ping` and `reset_all` no longer send the unused `session_id` field.
- Connect/disconnect messages are log records instead of unconditional prints, so they are
  not shown under a logging configuration above `INFO`.
- Plotter argument completion covers every directory position in a command.
- Documentation rewritten for the new plotter commands, the new output layout, the new
  export format, the startup prompts and the server's command replies.

## [0.1.0] - 2026-08-29

### Added

- Initial release: interactive client REPL, YAML experiment definitions with placeholder
  preprocessing, acquisition data handling, the `export` command and the plotting
  utilities.

[Unreleased]: https://github.com/vlsi-nanocomputing/FIREQ-Client/compare/version-0.1.1...HEAD
[0.1.1]: https://github.com/vlsi-nanocomputing/FIREQ-Client/compare/version-0.1.0...version-0.1.1
[0.1.0]: https://github.com/vlsi-nanocomputing/FIREQ-Client/releases/tag/version-0.1.0
