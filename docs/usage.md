# Usage

Before using the client, make sure the FIREQ server is already running. The client connects to the server over TCP, so the server must be started first.

For server-side setup and deployment details, see the [server](../../FIREQ-Server/docs/index.md) documentation.

The client is primarily used through an interactive command loop started by:

```bash
python run_client.py
```

The same features are available from Python scripts, see [Scripting](usage/scripting.md).

## Guideline

Below is the complete guideline reference.

```{toctree}
:maxdepth: 1

usage/command
usage/experiment_definition
usage/output
usage/plot
usage/scripting
```