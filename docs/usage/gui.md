# Graphical interface (FIREQ_GUI)

`FIREQ_GUI` is a PyQt6 experiment designer built on top of the client core
(`FIREQ_CLIENT`). An experiment is edited as a multi-track timeline of one shot, like an
audio or video editor; the GUI generates the YAML, runs it and lists the acquired data,
which can be plotted with the FIREQ_PLOTTER functions.

```bash
python -m pip install -r requirements-gui.txt
python run_gui.py                      # two-qubit template
python run_gui.py experiment.yaml      # open an existing YAML
python -m FIREQ_GUI.tools.mock_server  # simulated server on 127.0.0.1:5000 (no hardware)
```

## Window

| Area | Content |
| :--- | :--- |
| Centre — timeline | Block palette, tracks, ruler with the trigger FIFO entries (T1, T2 …) and the end of the shot (red line). |
| Left — *Acquired data* | File explorer of the output folder (`experiment_output/`), newest first, with the progress of the running experiment. Right-click an experiment to plot it. |
| Right — *Configuration* | *Properties* of the selected block or track, *Experiment* (board, shots, shot duration, preprocess macros, sweep variables), *Board* (channel map, IP allocation, Nyquist zones), *YAML* (live preview), *Checks*, *Log*. |

## Tracks

Tracks are named after the AMD RF Data Converter channels, `<tile>_<block>`
(`DAC 228_0`, `DAC 229_0`, `ADC 224_0` …); the SMA label printed on the board, when
there is one, is shown in the channel menus.

* **Drive track** (`DAC`) — one generator drive path. Its blocks are pulses with a
  carrier (MHz), an envelope (rectangular, Gaussian, DRAG, raised cosine, flat-top, half
  sine, triangle, custom expression), duration and gain; virtual-Z gates are thin
  markers. The carrier is one per generator (`$dfrequency`), so all pulses of a track
  must share it.
* **Readout track** (`DAC`) — one lane per frequency-multiplexed tone. Each tone has its
  own frequency, start, duration, gain and generator (readout path). The number of tones
  is limited by the generators of the bitstream.
* **Acquisition track** (`ADC`) — acquisition windows. *Copy to acquisition* creates
  the window of a tone: it starts at the tone start + time of flight (dashed arrow).
  Moving the tone moves its windows; moving a window changes its time of flight;
  resizing it changes the acquisition length. Each window uses one acquisition IP and
  demodulates at the tone frequency.

## Editing

* Drag a block from the palette onto a track, or double-click an empty track area.
* Drag a block to move it, drag its right edge to resize it; right-click for more
  actions (duplicate, repeat the same gate later, copy to acquisition, delete).
* `Del` deletes, `Ctrl+D` duplicates, `Ctrl+Z`/`Ctrl+Shift+Z` undo/redo, `Ctrl+wheel`
  zooms, `Shift+wheel` scrolls, *Fit* shows the sequence over the whole sweep.
* **Time base**: with *Snap to tick* every time is a multiple of the trigger generator
  clock period, `1 / 583.68 MHz ≈ 1.713 ns` (`trigger_clock_mhz` in `boards.json`). The
  status bar shows the cursor time in ns and ticks; the inspector shows every time in
  ticks.
* Every numeric property accepts a number, a `%macro` or a sweep expression such as
  `#tau + 200`. Blocks whose timing depends on an expression are drawn at the *Sweep
  preview* point with a dashed border and are edited in the inspector.

## Generated YAML

* `axisGeneratorIP_g` gets the drive track and/or the tone assigned to generator `g`
  (`_readout: false` pulses with the drive DAC `_dac_target`, a `_RECTANGULAR`
  `_readout: true` pulse with the readout DAC `_dac_target`).
* `axisAcquisitionIP_a` gets the window assigned to acquisition `a` (`$tof`,
  `$duration`, `$output_type`, demodulation at the tone frequency).
* Drive pulses fire on the drive trigger channel of their track. Tones that start
  together share a readout trigger channel; staggered tones get separate channels, and
  each window uses the channel of its tone.
* Absolute times are converted to the relative `$delay` entries of
  `axisTriggerGenerator_0`; linear sweep expressions are simplified (pulses at `100`,
  `#tau + 300`, `#2*tau + 500` → delays `100`, `#tau + 200`, `#tau + 200`).
* `$experiment_duration` is given, or automatic: end of the last event over the sweep
  plus a margin, rounded to the tick.

All files in `yaml_experiment_configurations_examples/` import and export again with
the same semantics (`tests/test_fireq_gui.py`).

## Acquired data and plotting

The left panel lists the output folder (`experiment_output/<name>/experiment_<timestamp>/`).
The run that just finished is selected automatically; below the tree a line summarises the
selected experiment (output type, sweep variables, shots, exported or not).

Right-click an experiment folder:

* *Line plot* (|S|, I and Q, I, Q) and *Heatmap* (|S|, phase, I, Q) call `_plot_2d` and
  `_plot_3d_heatmap` of `FIREQ_PLOTTER`; entries that do not apply to the experiment
  (e.g. a heatmap of a one-variable accumulated sweep) are disabled.
* Select two experiments with Ctrl+click to compare them: *IQ clouds |0> vs |1>*
  (`_plot_iq`) and *Spectra |0> vs |1>* (`_plot_spectr`).
* *Export* writes `data.pkl` for the plotter (same as the client `export` command), into
  `exported/` next to the output folder. Plot actions export automatically when needed.
* *Open configuration in the timeline* loads the `config.json` of the run back into the editor.
* *Save figures in the experiment folder* makes the plot functions save their PNG.

Double-click an experiment for its first applicable plot. Plots run in a separate Python
process, so the GUI stays responsive and several plot windows can be open. New plotting
functions only need an entry in `ACTIONS` in `FIREQ_GUI/core/plot_actions.py`.

## Hardware description

The number of generators and acquisitions, the converter channels and the trigger clock come
from a *hardware description*, with the same fields as an entry of
`FIREQ_GUI/core/boards.json`:

* built-in profiles (RFSoC 4x2, ZCU216) in `boards.json`;
* *File → Load hardware description…*: a JSON or YAML file, see
  `docs/examples/hardware_example.yaml`. Fields that are not given are taken from the board
  named in `base`, so a file can be as short as `base: ZCU216` and `generators: 4`;
* from the server: if the handshake contains a `hardware` field, or if the server answers
  *Server → Read hardware from server* (`{"cmd": "get_hardware"}` →
  `{"type": "hardware", "hardware": {...}}`), the description is applied automatically after
  the connection. The current FIREQ-Server does not send it yet; the simulated server does
  with `python -m FIREQ_GUI.tools.mock_server --hardware docs/examples/hardware_example.yaml`.

When a description is applied, tracks keep their channels if they still exist and are moved
to free channels otherwise. The description is saved in the project file.

## Boards

`FIREQ_GUI/core/boards.json` describes each board: converter channels, SMA labels, the
crossbar bit that reaches each DAC (`_dac_target`), the acquisition IPs fed by each ADC,
generator/acquisition counts and the trigger clock. Channels not routed by the current
bitstream are greyed out. To adapt the GUI to another bitstream, copy the file to
`~/.fireq_gui/boards.json` (or set `$FIREQ_GUI_BOARDS`) and edit it.

## Running

Connect with host/port/token in the toolbar, then **▶ Run** (F5). The YAML is written to
`experiments/<name>.yaml` and executed; results are saved in the folder shown in the
*Acquired data* panel, `experiment_output/<name>/experiment_<timestamp>/`, with the same files as the CLI, so
`export` and the plotter work unchanged. With several acquisition windows the first one
keeps the standard file names and the others are saved as
`data_<idx>__axisAcquisitionIP_<a>.pkl`. *reset_all before run* clears pulses and delays
left on the server by previous experiments.

## Tests

```bash
python -m unittest tests.test_fireq_gui                          # core, no Qt needed
QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_qt     # real GUI, synthetic mouse events
```
