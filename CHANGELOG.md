# Changelog

All notable changes to FIREQ-Client are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
tag names follow the `version-<major>.<minor>.<patch>` scheme.

## [Unreleased]

## [0.1.1] - 2026-09-15

### Breaking changes

- **Experiment data model and output layout changed.** A run now writes
  `experiment_summary.yaml` and `runtimes.yaml` at the experiment root, plus one
  subdirectory per acquisition IP (`axisAcquisitionIP_0/`, …) holding
  `data_<sweep indices>.pkl` and `.csv` per acquisition point. `config.json`,
  `var_order.json`, `end_message.json`, `data.pkl`, `configuration.json` and
  `exp_<i>.json` are no longer produced.
- **New server protocol required.** The client drives the `experiment_header` →
  `dma_package` → `iteration_ended` → `experiment_footer` flow; an older server fails fast
  with `TypeError: Unexpected message type from server while running experiment`.
- **`export` output changed**: one `data_<acquisition_ip_name>.pkl` + `.csv` per acquisition
  IP, with `experiment_summary.yaml` copied alongside. Experiment directories produced
  before this release contain none of those files and are silently skipped by `export`.
- **Plotter commands changed**: `plot_2d`, `plot_3d_heat` and `plot_iq_curve` now take only
  experiment directories. The mode tokens (`ri`, `r`, `i`, `p`, `f`) and the `save` token
  are gone, `plot_iq` was renamed `plot_iq_curve`, and `plot_spectr` was removed (its use
  case is `plot_2d dir1 dir2`).
- **`trigger_manually <generator_IP_name>`** now requires its argument and uses the message
  format `{"cmd": "trigger_manually", "ip_name": …}`.
- **Library users**: `Client.run()` was renamed `start()`.

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
- `runtimes.yaml` per run, holding the sweep time reported by the server.
- `retry? [y/n]` prompt when the initial connection fails, instead of aborting the client.
- Logging: the network workers accept an injected logger, and connect/disconnect and worker
  lifecycle messages are log records.
- `yaml_experiment_configurations_examples/drive_loopback.yaml` example experiment.

### Fixed

- Ctrl-C at the client prompt now disconnects gracefully instead of raising an unhandled
  `KeyboardInterrupt` traceback.
- `trigger_manually` ignored its argument and always triggered `/axisGeneratorIP_0`; it now
  honours the generator name, waits for the server and prints the reply.
- The receive worker could die on an unexpected error (malformed frame, `struct.error`),
  leaving the client hung in a wait with no diagnostic. It now logs, stops the worker and
  exits the loop.
- The send worker marked queue items as done even when a send failed, corrupting the queue's
  unfinished count; completion is only recorded after a successful send, and the worker
  tears down on any exception.
- Worker `stop()` no longer closes the socket — the client owns the socket lifetime —
  removing double-close/`EBADF` races during shutdown.
- A socket read timeout aborted the reader and discarded the original error; timeouts are
  retried and the cause is chained.
- The queue-polling helpers could block forever on a dead server; they now poll with a one
  second timeout.
- `export` with too few arguments printed a message and silently continued instead of
  returning.
- Connection timeout reduced from 60 s to 1 s, so an unreachable server reaches the retry
  prompt immediately.
- Plotter: heatmaps were unusable for three or more swept variables (the pinned index levels
  leaked into the image), data without a `shot` index level raised instead of plotting,
  `plot_iq_curve` limits were unstable under equal aspect, and the CDF analysis is skipped
  gracefully for datasets with fewer than two samples.

### Changed

- Connect/disconnect messages are log records instead of unconditional prints, so they are
  not shown under a logging configuration above `INFO`.
- Plotter argument completion covers every directory position in a command.
- Documentation rewritten for the new plotter commands, the new output layout and the new
  export format.

## [0.1.0] - 2026-08-29

### Added

- Initial release: interactive client REPL, YAML experiment definitions with placeholder
  preprocessing, acquisition data handling, the `export` command and the plotting
  utilities.

[0.1.1]: https://github.com/vlsi-nanocomputing/FIREQ-Client/compare/version-0.1.0...version-0.1.1
[0.1.0]: https://github.com/vlsi-nanocomputing/FIREQ-Client/releases/tag/version-0.1.0
