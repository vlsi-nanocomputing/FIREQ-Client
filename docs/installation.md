# Installation

FIREQ-Client is implemented in Python and can be installed locally in a virtual environment. The current documentation assumes Python 3.11.7.

## Requirements

The client depends on the packages listed in the repository requirements file. A typical setup is:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell, the activation command is:

```powershell
.\.venv\Scripts\Activate.ps1
```

## Running the client

Before launching the client, make sure you know the board's server address and port. `run_client.py` prompts for both values at startup; pressing Enter uses `0.0.0.0` and `5000` as the defaults.

Now, the client can be started with:

```bash
python run_client.py
```

The startup script currently points to the default server address and port defined in the repository. These values can be adjusted before launching the client if needed.

