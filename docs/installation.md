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

Before launching the client, open `run_client.py` and set the server address
and port to match the board configuration:

```python
SERVER_IP = "<board-ip>"
SERVER_PORT = 5000
```

Now, the client can be started with:

```bash
python run_client.py
```

The startup script currently points to the default server address and port defined in the repository. These values can be adjusted before launching the client if needed.

