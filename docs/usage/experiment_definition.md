# Client Experiment Configuration & Execution

Experiments in the FIREQ platform are defined using a structured YAML specification and
executed through the interactive client CLI. This document describes the YAML structure,
parameter syntax, node configuration schemas, generated files, and the complete execution
workflow.

---

## 1. YAML Configuration Architecture

Experiment definitions are stored in YAML format (for example, in
`yaml_experiment_configurations_examples/`). The input directory is not created by the
client. A definition has three top-level sections:

1. **`preprocess`**: Defines static constants and reusable macros.
2. **`variables`**: Specifies parameter sweeps using linear ranges or explicit lists.
3. **`sys_config`**: Describes the target hardware configuration tree and node parameter callbacks.

### Syntax & Prefix Rules

The YAML parser relies on specialized prefixes to interpret values and construct the hardware execution graph:

* **`%` (Preprocess Macro):** Resolved client-side from the `preprocess` block before
  sending the configuration to the server (for example, `%DEFAULT_PULSE_LEN`). Tokens
  are resolved recursively in values under `sys_config` and `variables`. A value that
  consists of one token (`"%gain_r"`) keeps the type of the referenced value. A token
  embedded in a longer string, including a `#` expression, is replaced by its text:
  `"#%var1 - %var2 + 100"` becomes `"#1000 - 500 + 100"`. An undefined name is an
  error.
* **`#` (Sweep Reference):** Binds a parameter to a sweepable dynamic variable defined in the `variables` section (e.g., `#freq`, `#gain`).
* **`$` (Dynamic Hardware Parameter):** Maps directly to runtime parameter callbacks on server-side hardware nodes (e.g., `$duration`, `$gain`, `$rfrequency`).
* **`_` (Static Node Property):** Specifies immutable structural metadata set during node instantiation (e.g., `_name`, `_envelope`, `_readout`).
* **`/` (Hardware Node Path):** Identifies a target FPGA IP node within the system tree (e.g., `/axisGeneratorIP_0`).

---

## 2. Hardware Node Configuration Reference

This section details all available parameters, default behavior, and structural requirements for each node type in the `sys_config` hierarchy. Every experiment must also define the required top-level `sys_config.$shots` integer, which sets the number of acquisitions per sweep point.

### 2.1 Acquisition Node (`/axisAcquisitionIP_*`)

Configures signal demodulation, output processing modes, and trigger timing.

| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `$duration` | `float` | Yes | Acquisition window duration in nanoseconds (`ns`) |
| `$output_type` | `str` | Yes | Data output mode: `"raw"`, `"decimated"`, or `"accumulated"` |
| `$rfrequency` | `float` | Yes | Demodulation frequency in `MHz` |
| `$rphase` | `float` | Yes | Demodulation initial phase in radians |
| `$rchannel` | `int` | Yes | Trigger channel (`0` deactivates external trigger) |
| `$tof` | `float` | Yes | Time-of-Flight calibration delay in nanoseconds (`ns`) |

---

### 2.2 Signal Generator Node (`/axisGeneratorIP_*`)

Manages signal generation, RF frequency synthesis, and child definitions for pulse envelopes, pulse execution, and virtual gates.

#### Node-Level Parameters

| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `$dfrequency` | `float` | Yes | Drive carrier frequency in `MHz` |
| `$rfrequency` | `float` | Yes | Readout carrier frequency in `MHz` |
| `$rphase` | `float` | Yes | Readout phase in radians |
| `$rchannel` | `int` | Yes | Readout trigger channel (`0` to deactivate) |
| `$dchannel` | `int` | Yes | Drive trigger channel (`0` to deactivate) |
| `$lfsr_seed` | `int` | No | Seed value for the PRBS / LFSR generator |
| `$drive_order` | `list[str]` | No | Sequence list of pulse names to be executed |

#### Child Node: Custom Envelope (`envelope`)
Defines arbitrary IQ pulse waveforms stored in generator memory.

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `_name` | `str` | Required | Unique name (`_RECTANGULAR` is a reserved system keyword) |
| `_for_interpolation` | `bool` | `False` | Uses hardware interpolation engine |
| `_is_symmetric` | `bool` | `False` | Enables symmetry optimization (requires `_for_interpolation=True`) |
| `_i_even` | `bool` | `None` | Symmetry parity for I channel (required if `_is_symmetric=True`) |
| `_q_even` | `bool` | `None` | Symmetry parity for Q channel (required if `_is_symmetric=True`) |
| `$samples` | `list[complex]` | Required | Array of normalized complex IQ values within $[-1.0, 1.0]$ |

#### Child Node: Pulse (`pulse`)
Associates a pulse envelope with dynamic timing, scaling, and hardware crossbar target routing.

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `_name` | `str` | Required | Unique identifier for the pulse gate |
| `_readout` | `bool` | `False` | Flags the pulse as a readout trigger pulse |
| `_envelope` | `str` | Required | Target registered envelope name |
| `_switch_iq` | `bool` | `False` | Swaps I and Q hardware output channels |
| `_keep_last` | `bool` | `False` | Holds the last waveform sample level at pulse completion |
| `_dac_target` | `int` | `1` | DAC target mask for crossbar matrix routing |
| `$duration` | `float` | Required | Pulse duration in nanoseconds (`ns`) |
| `$gain` | `float` | Required | Normalized amplitude gain magnitude between `-1.0` and `1.0` |

#### Child Node: Virtual Z Gate (`vzgate`)
Executes instantaneous software frame phase shifts without RF emission.

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `_name` | `str` | Required | Unique identifier for the gate |
| `_readout` | `bool` | `False` | Applies phase shift to readout frame if `True` |
| `$vz_rotation` | `float` | Required | Phase rotation angle normalized to $2\pi$ |

---

### 2.3 Trigger Generator Node (`/axisTriggerGenerator_*`)

Coordinates master timing frame periods and delayed hardware triggers.

#### Node-Level Parameters

| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `_name` | `str` | Yes | Name of the trigger generator instance |
| `$experiment_duration` | `float` | Yes | Total repetition interval / frame period in nanoseconds (`ns`) |

#### Child Node: Delay Item (`delay`)
Specifies channel-specific trigger delay offsets.

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `_name` | `str` | Required | Identifier for the delay instance |
| `_ttype` | `str` | Required | Trigger target type: `"drive"` or `"readout"` |
| `_channel_mask` | `int` | Required | Channel bitmask target |
| `_index` | `int` | Optional | Sequence index for multi-drive delay tracking |
| `_generate_trigger` | `bool` | `False` | Fires a physical trigger upon delay counter expiration |
| `$delay` | `float` | Required | Timing delay value in nanoseconds (`ns`) |

---

## 3. Mutable Parameters & State Retention

When a parameter is declared or modified through a YAML configuration, it remains persistent in server memory associated with its declared `_name` for the duration of the interactive session.

* **Incremental Modifications:** If a pulse `rect_d` is already initialized on the server, subsequent experiment configurations can update its parameters (e.g., changing `$duration` or `$gain`) simply by referencing `rect_d` without re-specifying static properties (`_envelope`, `_dac_target`).
* **State Consistency:** Because both client and server maintain state incrementally, changing hardware routing or resetting parameter states between disparate experiments may require executing `reset_all` from the interactive CLI to avoid state pollution.

---

## 4. Complete Experiment Example

Below is a complete specification incorporating preprocess variables, dynamic sweep parameter references, pulse definitions, and timing delays:

This example performs a two-dimensional readout sweep over frequency and pulse gain. For
each sweep point, FIREQ:

- generates a 1 µs rectangular readout pulse;
- sweeps the readout frequency from 2 to 3 GHz over 50 points;
- sweeps the normalized pulse gain from 0.05 to 0.5 over 20 points;
- demodulates the acquired signal at the same swept frequency;
- accumulates the resulting I/Q samples over a 1.5 µs acquisition window;
- compensates for a 160 ns time-of-flight delay;
- repeats the measurement for 1000 shots;
- uses a 102 µs experiment repetition period.

Using `#freq` in both the generator and acquisition configuration means the signal is
coherently demodulated at the same frequency used for excitation.

```yaml
preprocess:
  RO_TIME: 1500
  TOF_VAL: 160

variables: 
  freq:
    start: 2000
    stop: 3000
    num: 50
    mode: lin
  gain:
    start: 0.05
    stop: 0.5
    num: 20
    mode: lin

sys_config:
  $shots: 1000

  /axisGeneratorIP_0:
    $dfrequency: 0
    $rfrequency: "#freq"
    $rphase: 0.0
    $rchannel: 1
    $dchannel: 0
    pulse:
      - _name: rect
        _readout: true
        _envelope: _RECTANGULAR
        _dac_target: 1
        $duration: 1000.0
        $gain: "#gain"

  /axisAcquisitionIP_0:
    $duration: "%RO_TIME"
    $output_type: accumulated
    $rfrequency: "#freq"
    $rphase: 0
    $rchannel: 1
    $tof: "%TOF_VAL"

  /axisTriggerGenerator_0:
    $experiment_duration: 102000.0
    delay:
      - _name: readout_delay_0
        _ttype: readout
        _channel_mask: 1
        _index: 1
        $delay: 100
```

### Sweep definitions

Each entry in `variables` must specify a mode:

```yaml
variables:
  frequency:
    mode: lin
    start: 2000
    stop: 3000
    num: 50
  gain:
    mode: list
    values: [0.05, 0.1, 0.2, 0.5]
```

`lin` uses `start`, `stop`, and `num` and produces `num` linearly spaced values,
including the endpoints. `list` uses `values` exactly as written. A variable is swept
only when it is referenced by a `#name` expression in `sys_config`; unused variables
are still recorded in the experiment header but do not add iterations. The server
determines `var_order` from the callbacks used by the configuration. That order is
the outer-to-inner loop order and is recorded in `experiment_summary.yaml`.

## Generated output

By default, the client writes runs below `experiment_output`, relative to the directory
from which `run_client.py` was started. The client asks for the output directory at
startup and logs its absolute path after connecting. For an experiment whose YAML file
is named `loopback.yaml`, a run has this layout:

```text
experiment_output/loopback/experiment_<timestamp>/
├── axisAcquisitionIP_0/
│   ├── data_0.pkl
│   ├── data_0.csv
│   ├── data_1.pkl
│   ├── data_1.csv
│   └── ...
├── experiment_summary.yaml
└── runtimes.yaml
```

There is one acquisition subdirectory for every acquisition IP that sends data. A
non-swept experiment has `data_0.pkl` and `data_0.csv`; a run with two swept variables
has names such as `data_0_0.pkl` and `data_0_1.csv`. The indices are zero-based
positions in `var_order`, not the variable values. For the example above, 50 frequency
values and 20 gain values produce 1000 iterations and, when the acquisition is active,
1000 file pairs in `axisAcquisitionIP_0/`.

Each per-point pickle contains a pandas dataframe. The CSV is the same dataframe in
text form, including its index:

| Field | Meaning |
| :--- | :--- |
| `shot` | Zero-based shot index. |
| `time` | Zero-based sample index within a shot. `accumulated` has one sample (`0`) per shot; `raw` and `decimated` contain a time trace. |
| `value` | Complex IQ sample (`real + 1j * imag`). |

The `raw` and `decimated` modes differ in the hardware stream and therefore in the
number of `time` samples per shot; the exact count depends on the acquisition duration
and hardware sampling/decimation. `accumulated` returns one integrated value per shot.

`experiment_summary.yaml` contains the experiment name, the full server experiment
header, the resolved configuration, `var_order`, the values for each variable, and
`expected_iterations`. `runtimes.yaml` contains `runtimes` (one duration in nanoseconds
per iteration), `run_time` (the total duration in nanoseconds), and `aborted`. If
`aborted` is `true`, the data and per-point files are partial.

## Running an experiment

The server must be running and reachable before the client connects. Use two terminals:

1. Prepare the server and board according to the server documentation, then start it
   from the `FIREQ-Server` directory:

   ```bash
   python start_server.py
   ```

2. In `FIREQ-Client`, configure the server address and port in `run_client.py` if
   necessary, activate the client virtual environment, and start the client:

   ```bash
   python run_client.py
   ```

3. At the client prompt, optionally apply the configuration without acquiring data:

   ```text
   config_yaml yaml_experiment_configurations_examples/<experiment_name>.yaml
   ```

   This validates and applies the configuration, but does not run the sweep. Use
   `reset_all` between unrelated experiments when server-side state should not be
   retained.

4. Start the experiment:

   ```text
   run_yaml yaml_experiment_configurations_examples/<experiment_name>.yaml
   ```

   The client resolves `%` placeholders, sends the resolved `variables` and
   `sys_config`, receives the experiment header, and streams each DMA package into
   the appropriate acquisition directory. The progress count is the number of sweep
   points, not the number of shots.

5. After the prompt returns, inspect the run directory shown by the client:

   * open `experiment_summary.yaml` and verify `var_order`, `var_values`,
     `expected_iterations`, and the resolved `config`;
   * check that each acquisition IP has the expected `.pkl`/`.csv` pairs and that
     their index suffixes cover the sweep points;
   * load one pickle with `pandas.read_pickle` (or open its CSV) and verify the
     `shot`, `time`, and `value` fields and the expected number of shots;
   * open `runtimes.yaml` and check `aborted`, the number of `runtimes`, and
     `run_time`. Treat an aborted run as incomplete even if some files exist.

6. To combine per-point files into one dataframe per acquisition IP, run the
   following at the client prompt:

   ```text
   export experiment_output/<experiment_name>/experiment_<timestamp>/ exported/<experiment_name>/
   ```

   The exported directory contains `data_<acquisition_ip_name>.pkl`,
   `data_<acquisition_ip_name>.csv`, and a copy of `experiment_summary.yaml`. The
   exported dataframe is indexed by `var_order`, `shot`, and `time`, and is the input
   expected by the plotter. Plotting is optional; see [Plotting and Post-Processing](plot.md).