# FIREQ-Client
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22161722.svg)](https://doi.org/10.5281/zenodo.22161664)
[![GitHub Release](https://img.shields.io/github/v/release/vlsi-nanocomputing/FIREQ-Server)](https://github.com/vlsi-nanocomputing/FIREQ-Server/releases/latest)

FIREQ-Client is the Python client-side component of the FIREQ platform.

It provides the user-facing interface used to configure, launch, and monitor
FIREQ experiments through the FIREQ-Server.

## Role in FIREQ

FIREQ is organized into three main repositories:

- [`FIREQ-Client`](https://github.com/vlsi-nanocomputing/FIREQ-Client): Python client interface.
- [`FIREQ-Server`](https://github.com/vlsi-nanocomputing/FIREQ-Server): Python server and control layer.
- [`FIREQ`](https://github.com/vlsi-nanocomputing/FIREQ): HDL firmware for RFSoC 4x2 and ZCU216 board.

This repository contains the client-side software used to interact with the
FIREQ platform.

## Documentation

The complete FIREQ documentation is available at:

https://vlsi-nanocomputing.github.io/FIREQ-docs/

Client-specific documentation is maintained in this repository under:

```text
docs/
```

The central FIREQ documentation site automatically imports the content of this
directory and publishes it together with the documentation of the server and
firmware repositories.

## Repository structure

```text
FIREQ-Client/
├── FIREQ_CLIENT/                                    Python client package
├── FIREQ_PLOTTER/                                   Plotting utilities and REPL
├── FIREQ_GUI/                                       PyQt6 experiment designer and live viewer
├── yaml_experiment_configurations_examples/         Example experiment YAML configurations
├── testing/                                         Test scripts
├── docs/                                            Client-specific documentation
├── run_client.py                                    Minimal client entry point
├── run_plotter.py                                   Plotter entry point
├── run_gui.py                                       Graphical interface entry point
├── requirements.txt                                 Python dependencies
└── README.md
```

## Getting started

Clone the repository:

```bash
git clone https://github.com/vlsi-nanocomputing/FIREQ-Client.git
cd FIREQ-Client
```

Create a Python virtual environment:

```bash
python -m venv .venv
```

Activate the virtual environment:

```bash
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Upgrade `pip` inside the virtual environment:

```bash
python -m pip install --upgrade pip
```

Install the required dependencies, either for the command-line client only or for the client with the graphical
interface (Python >= 3.10; the boards run 3.10.4, see `docs/installation.md` for a Miniconda setup):

```bash
python -m pip install -r requirements.txt        # client only
python -m pip install -r requirements-gui.txt    # client + GUI
```

## Quick start

A minimal client execution can be started with:

```bash
python run_client.py
```

### Graphical interface

`FIREQ_GUI` is a PyQt6 experiment designer built on top of the client core. The shot is
edited as a multi-track timeline (one track per converter channel, e.g. `DAC 228_0`,
`DAC 229_0`, `ADC 224_0`) with drag-and-drop pulse blocks, frequency-multiplexed readout
tones and acquisition windows delayed by the time of flight, all on the 1.713 ns trigger
grid. It generates the experiment YAML, runs it and lets you browse and plot the acquired data.

```bash
python run_gui.py                       # or: python run_gui.py my_experiment.yaml
python -m FIREQ_GUI.tools.mock_server   # simulated server on 127.0.0.1:5000 for testing
```

Details in `docs/usage/gui.md`.

Example experiment YAML configurations are available in:

```text
yaml_experiment_configurations_examples/
```

## Contributing and Support

Contributions to FIREQ-Client are welcome.

You can contribute by:

- improving the client implementation;
- adding or updating examples in `yaml_experiment_configurations_examples/`;
- improving the documentation in `docs/`;
- reporting bugs or unexpected behavior;
- suggesting new features or use cases.

If you find a problem, please open an issue on GitHub:

https://github.com/vlsi-nanocomputing/FIREQ-Client/issues

If you have questions, ideas, or need support, feel free to create a discussion
or open an issue on GitHub.

Client-specific documentation should be updated in:

```text
docs/
```

The central documentation site automatically imports the content of this
repository's `docs/` directory and publishes it together with the documentation
of the server and firmware repositories.
