# Plotting and Post-Processing

The plotter is an interactive prompt that draws the data of exported experiments. Every
command takes only the experiments to draw: what is displayed — the quantity, the axes and
the parameters — is chosen while the plot is open, from selectors in its windows.

---

## 1. Overview

Each command takes one or more directories exported by the client's `export` command (see
the [interactive command reference](command.md)) and opens one pair of windows per
acquisition IP.

| Command | Plot | Selectors | With several directories |
| :--- | :--- | :--- | :--- |
| `plot_2d` | One quantity of the acquired signal against one swept variable. | `quantity`, `x-axis` | One curve per directory, with the directory names in the legend. |
| `plot_3d_heat` | A heatmap of one quantity over two swept variables. | `quantity`, `x-axis`, `y-axis`, `dataset` | One directory at a time, picked with the `dataset` selector. |
| `plot_iq_curve` | The acquired signal on the complex plane. | `parameter` | One scatter series per directory, plus a single-shot readout fidelity analysis when exactly two directories are given. |

Every swept variable that is not used as an axis, or as the curve parameter of
`plot_iq_curve`, gets a slider, so experiments with more sweep variables than the plot has
dimensions stay fully explorable.

---

## 2. Data the Plotter Expects

### 2.1 Exported directories

The plotter reads exported experiments only, and an exported directory is the argument of
every command. Exporting is done with the client's `export` command, which writes one
pickle per acquisition IP:

```text
exported/experiment_20260914_122614/
├── data_axisAcquisitionIP_0.csv
├── data_axisAcquisitionIP_0.pkl
├── data_axisAcquisitionIP_1.csv
├── data_axisAcquisitionIP_1.pkl
└── experiment_summary.yaml
```

Every file named `data_<acquisition_ip_name>.pkl` found in the directory is loaded, and the
acquisition IP name is the part between `data_` and `.pkl`. The matching `.csv` files and
`experiment_summary.yaml` are not read by the plotter.

### 2.2 Dataframe layout

Each pickle holds one dataframe, for one acquisition IP, indexed by the sweep variables of
the experiment together with `shot` and `time`, with a single complex `value` column
holding the in-phase and quadrature components:

```text
drive_freq_sweep  shot  time  value
2500.0            0     0     (3+5j)
2500.0            0     1     (2+4j)
...
```

- Index levels that hold a single value are treated as **fixed**, not swept: they are not
  offered as axes and get no slider. This is why a heatmap needs two sweep variables with
  more than one value each.
- `plot_2d` needs at least one swept variable and `plot_3d_heat` needs two. An
  acquisition IP without them cannot be plotted and is reported instead, for example
  `Not enough axes in data to plot in 2D` or `... to plot a heatmap`. `plot_iq_curve`
  also plots data with no swept variable, as the raw shots (see section 4.3).
- The `shot` level is averaged before plotting, so each plotted point is the mean over the
  shots acquired there. The readout fidelity analysis of `plot_iq_curve` is the exception:
  it uses the individual shots.
- `time` is swept for acquisitions recorded in the `raw` or `decimated` output modes. In
  `accumulated` mode the trace is integrated on-chip and `time` holds a single sample.
- Dataframes plotted together must share the same index levels and swept variables;
  otherwise the plotter reports the mismatch and plots none of that acquisition IP.

---

## 3. Running the Plotter

### 3.1 Starting the prompt

Run the plotter from the repository root:

```bash
python run_plotter.py
```

It opens an interactive prompt (`> `). `quit`, `exit`, `Ctrl-C` and `Ctrl-D` leave it.

### 3.2 Commands

| Command | Argument | Description |
| :--- | :--- | :--- |
| `plot_2d` | `<dir> [<dir> ...]` | Plots a quantity of the signal against a swept variable. |
| `plot_3d_heat` | `<dir> [<dir> ...]` | Plots a heatmap of a quantity over two swept variables. |
| `plot_iq_curve` | `<dir> [<dir> ...]` | Plots the acquired signal on the complex plane. |
| `quit`, `exit` | — | Leaves the plotter. |

The arguments are exported experiment directories, and nothing else: there is no mode or
option to type. Paths are completed with Tab at every argument position.

### 3.3 Windows, selectors and sliders

Every command opens two windows per acquisition IP:

- the **plot window**, holding the axes, one slider per remaining swept variable and the
  standard matplotlib toolbar — which is also how a figure is saved;
- the **controls window**, titled `<acquisition_ip_name> controls`, holding the selectors
  listed in the command reference below.

Closing one of the two windows closes only that window; the other stays open, and the
selectors stop acting on the plot once the plot window is closed.

The sliders step through the values that the data actually holds and display the value they
point at. When several directories are plotted together, a slider spans the values of all
of them, and each data set is read at the value closest to the one selected.

### 3.4 Errors and limits

- One plot window is opened per acquisition IP name, in alphabetical order, and the prompt
  reports the ones that were plotted.
- An acquisition IP that cannot be plotted is reported and skipped, while the remaining
  ones are still plotted:

```text
> plot_3d_heat exported/experiment_20260914_122614/
Failed to plot axisAcquisitionIP_1: ValueError: Not enough axes in data to plot a heatmap
plotted 1 acquisition IP(s): axisAcquisitionIP_0
```

- A mistyped command or path is reported and the prompt returns:

```text
> plot_2d /does/not/exist
plot_2d failed: ValueError: not a directory: /does/not/exist
```

---

## 4. Command Reference

### 4.1 `plot_2d`

Plots one quantity of the signal against one swept variable.

```text
> plot_2d exported/experiment_20260914_122614/
plotted 2 acquisition IP(s): axisAcquisitionIP_0, axisAcquisitionIP_1
```

| Selector | Options | Notes |
| :--- | :--- | :--- |
| `quantity` | magnitude, phase, phase (unwrapped), real, imag, real + imag | The quantity drawn against the x axis. `real + imag` draws the two components at once, as a solid and a dashed curve; `phase (unwrapped)` removes the jumps of the raw phase. |
| `x-axis` | the swept variables of the data | The variable placed on the x axis. Every other swept variable gets a slider. |

The plot starts on `magnitude`, with the last swept variable on the x axis.

### 4.2 `plot_3d_heat`

Plots a heatmap of one quantity over two swept variables. It needs two swept variables;
acquisition IPs with fewer are skipped as shown in section 3.4.

```text
> plot_3d_heat exported/experiment_20260914_122614/
Failed to plot axisAcquisitionIP_1: ValueError: Not enough axes in data to plot a heatmap
plotted 1 acquisition IP(s): axisAcquisitionIP_0
```

Here only the first acquisition IP holds two swept variables, so only it is drawn as a
heatmap; the second one needs more than one axis and is reported instead.

| Selector | Options | Notes |
| :--- | :--- | :--- |
| `quantity` | magnitude, phase, real, imag | The plotted quantity, each with its own colour map. |
| `x-axis` | the swept variables of the data | The variable placed on the x axis. |
| `y-axis` | the swept variables of the data | The variable placed on the y axis. Picking a variable that is already on the other axis swaps the two axes. |
| `dataset` | the directory names | Which experiment is displayed. It appears only when more than one directory is given. |

The plot starts on `magnitude`, with the last swept variable on the x axis and the one
before it on the y axis. Every remaining swept variable gets a slider.

### 4.3 `plot_iq_curve`

Plots the acquired signal on the complex plane, one scatter series per directory. As
described in section 2.2, the values are averaged over the shots, so each series holds one
point per value of the remaining sweep variables.

| Selector | Options | Notes |
| :--- | :--- | :--- |
| `parameter` | the swept variables of the data | The variable that defines the curve. Every other swept variable gets a slider. |

The plot starts with the last swept variable as the curve parameter.

For data with no sweep variable, such as the state measurements of `IQ_at_0.yaml` and
`IQ_at_1.yaml`, the plotter scatters the raw shots of each directory, with no `parameter`
selector, no slider and no controls window.

#### Readout fidelity

When exactly two directories are given, the first holding the shots prepared in state 0 and
the second those prepared in state 1, the plotter computes the single-shot readout fidelity
once the IQ window is closed:

```text
> plot_iq_curve exported/state_0/ exported/state_1/
readout fidelity axisAcquisitionIP_0: assignment 1-(ε0+ε1)/2 = 93.45% | separation 1-ε0-ε1 = 86.90% | ε0=P(1|0)=6.10% ε1=P(0|1)=7.00% | threshold 1.234e+03 (rotated axis) | shots 10000/10000
plotted 1 acquisition IP(s): axisAcquisitionIP_0
```

The analysis uses the un-averaged shots:

1. Both clouds are rotated about the midpoint of their means, so that the line joining the
   means is horizontal, with state 1 on the right.
2. A shot is read as 0 when its rotated real part is at or below the threshold, and as 1
   above it. The threshold is the midpoint between consecutive distinct shot values that
   maximises `CDF₀(t) − CDF₁(t)`.
3. The error rates at the threshold are `ε₀ = P(read 1 | prepared 0)` and
   `ε₁ = P(read 0 | prepared 1)`. Each is computed over the shots of its own state.

| Quantity | Formula | Range |
| :--- | :--- | :--- |
| Assignment fidelity | `1 − (ε₀ + ε₁)/2` | 0.5 – 1 |
| Separation | `1 − ε₀ − ε₁` | 0 – 1 |

A second window shows the rotated clouds with the threshold, and the cumulative
distributions along the rotated axis, with the legend `assignment <a>% / separation <s>%`.

The analysis requires, in both directories, no swept variable other than `shot` and one
sample per shot (`accumulated` mode). Otherwise it is skipped with a message and the IQ plot
is still drawn:

```text
readout fidelity axisAcquisitionIP_0: skipped, state_0 sweeps ['amp'], it needs the shots of a single configuration
readout fidelity axisAcquisitionIP_0: skipped, state_0 holds 1024 samples per shot (index level 'time'), it needs one sample per shot (accumulated mode)
```

---

## 5. Plotting Several Experiments Together

When more than one directory is given, the plotter plots the acquisition IP names present
in **all** of the directories, and only those. What "together" means depends on the
command:

| Command | Behaviour with several directories |
| :--- | :--- |
| `plot_2d` | One curve per directory on the same axes. The legend shows the directory names, labelling each component of `real + imag` as `<directory> · <component>`. |
| `plot_3d_heat` | One directory at a time, selected in the controls window. The title shows which directory is displayed. |
| `plot_iq_curve` | One scatter series per directory, labelled with the directory names. |

The directories must describe the same measurements: they have to share the same index
levels and the same swept variables, otherwise the plotter reports the mismatch and plots
none of that acquisition IP.
