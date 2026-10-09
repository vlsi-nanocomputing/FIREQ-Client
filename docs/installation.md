# Installation

FIREQ-Client is implemented in Python and can be installed locally in a virtual environment. The reference version is
Python 3.10.4, the same version that runs on the boards; any Python >= 3.10 works.

The repository provides two dependency sets:

| Environment | Requirements file | `pyproject.toml` extra | Provides |
|---|---|---|---|
| Client only | `requirements.txt` | (none) | command-line client (`run_client.py`), export, `FIREQ_PLOTTER` |
| Client + GUI | `requirements-gui.txt` | `gui` | everything above plus the graphical experiment designer (`run_gui.py`): PyQt6 and pyqtgraph |

`requirements-gui.txt` includes `requirements.txt`, so the GUI environment is a superset of the client one.

## Creating the environment

### With `venv`

Use this when the system Python is already 3.10 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

On Windows PowerShell, the activation command is:

```powershell
.\.venv\Scripts\Activate.ps1
```

### With Miniconda

Use this when the system Python is a different version, or to match the boards exactly:

```bash
conda create -n fireq python=3.10.4
conda activate fireq
python -m pip install --upgrade pip
```

To remove the environment later: `conda deactivate`, then `conda remove -n fireq --all`.

## Installing the dependencies

From the repository root, with the environment active, install **one** of the two sets.

Client only:

```bash
python -m pip install -r requirements.txt
```

Client + GUI:

```bash
python -m pip install -r requirements-gui.txt
```

The same sets can be installed from `pyproject.toml` (editable install): `pip install -e .` for the client only,
`pip install -e ".[gui]"` for the client with the GUI.

```{note}
With Python 3.10, pandas must stay on the 2.x series (pandas 3 requires Python >= 3.11) and matplotlib below 3.11;
the requirement files and `pyproject.toml` already pin these ranges.
```

## Running the client

Before launching, make sure you know the board's server address and port.

### Command-line client

```bash
python run_client.py
```

`run_client.py` prompts for the address and port at startup; pressing Enter uses `0.0.0.0` and `5000` as the defaults.
Experiments are then run from the interactive prompt (see [Usage](usage.md)).

### Graphical interface

Requires the client + GUI environment.

```bash
python run_gui.py                    # start from the default template
python run_gui.py experiment.yaml    # open an existing experiment YAML
```

Server address, port and token are entered in the toolbar before pressing **Connect**; they are remembered for the next
session. The GUI uses the same client code to talk to the server, so the command-line client is not needed while the
GUI is running. See [the GUI guide](usage/gui.md) for details.

### Testing without hardware

A simulated server is available to try the GUI without a board:

```bash
python -m FIREQ_GUI.tools.mock_server                 # listens on 127.0.0.1:5000
python -m FIREQ_GUI.tools.mock_server --hardware docs/examples/hardware_example.yaml
```

Then connect to `127.0.0.1`, port `5000`. The `--hardware` option makes the simulated server announce a hardware
description (number of generators and acquisitions, channels), as the real server will do after connection.

To check the installation of the GUI environment, run the tests (no display needed):

```bash
QT_QPA_PLATFORM=offscreen python -m unittest tests.test_fireq_gui
QT_QPA_PLATFORM=offscreen python tests/test_gui_qt.py
```
