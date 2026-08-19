# Client Experiment Configuration & Execution

Experiments in the FIREQ platform are defined using a structured YAML specification and executed through the interactive client CLI. This document details the YAML file structure, parameter syntax rules, node configuration schemas, and the operational workflow.

---

## 1. YAML Configuration Architecture

Experiment definitions are stored in YAML format (typically within the `experiments/` directory) and structured into three primary top-level sections:

1. **`preprocess`**: Defines static constants and reusable macros.
2. **`variables`**: Specifies parameter sweeps (e.g., linear or logarithmic ranges).
3. **`sys_config`**: Describes the target hardware configuration tree and node parameter callbacks.

### Syntax & Prefix Rules

The YAML parser relies on specialized prefixes to interpret values and construct the hardware execution graph:

* **`%` (Preprocess Macro):** Resolved client-side from the `preprocess` block before sending configurations to the server (e.g., `%DEFAULT_PULSE_LEN`).
* **`#` (Sweep Reference):** Binds a parameter to a sweepable dynamic variable defined in the `variables` section (e.g., `#freq`, `#gain`).
* **`$` (Dynamic Hardware Parameter):** Maps directly to runtime parameter callbacks on server-side hardware nodes (e.g., `$duration`, `$gain`, `$rfrequency`).
* **`_` (Static Node Property):** Specifies immutable structural metadata set during node instantiation (e.g., `_name`, `_envelope`, `_readout`).
* **`/` (Hardware Node Path):** Identifies a target FPGA IP node within the system tree (e.g., `/axisGeneratorIP_0`).

---

## 2. Hardware Node Configuration Reference

This section details all available parameters, default behavior, and structural requirements for each node type in the `sys_config` hierarchy.

### 2.1 Acquisition Node (`/axisAcquisitionIP_*`)

Configures signal demodulation, output processing modes, and trigger timing.

| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `_name` | `str` | Yes | Name of the acquisition node instance |
| `_ll_handler` | `AcquisitionDriver` | Internal | Low-level driver handler |
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
| `_name` | `str` | Yes | Name of the signal generator instance |
| `_ll_handler` | `GeneratorDriver` | Internal | Low-level driver handler |
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
| `_channel_mask` / `_channel` | `int` | Required | Channel index or bitmask target |
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

## Running an experiment

To execute an experiment using the FIREQ environment

1. start the FIREQ server first:

```bash
python API.py
```

2. start the client:

```bash
python run_client.py
```

4. run an experiment:

```text
run_yaml experiments/<experiment_name>.yaml
```

5. wait for the acquisition to complete,
6. inspect the generated output files.