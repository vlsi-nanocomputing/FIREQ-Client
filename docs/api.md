# FIREQ Client Architecture & Reference

Welcome to the `FIREQ-Client` API reference and architecture guide. The client acts as the front-end workstation interface that connects to the `FIREQ-Server`, processes experiment definitions, manages non-blocking TCP streams, and visualizes acquisition results.

---

## Architectural Overview

The client framework is organized into three main operational layers:

1. **Connection Layer**: Establishes and manages non-blocking TCP socket sessions with the server.
2. **Protocol & Networking Layer**: Implements length-prefixed binary framing  and manages background I/O threads.
3. **Experiment & Processing Layer**: Resolves YAML templates, reconstructs raw binary DMA acquisition payloads into numerical arrays, and renders/exports dataset plots.

---

## Interactive & Runtime Lifecycle

A standard client runtime lifecycle proceeds as follows:

1. **Instantiation & Connection**: The `Client` instance connects to the specified `<SERVER_IP>:<SERVER_PORT>` endpoint.
2. **Handshake & Auth**: Performs authentication via shared token validation.
3. **YAML Preprocessing**: Loads `.yaml` experiment definitions and resolves internal variable placeholders.
4. **Command Execution**: Dispatches execution requests.
5. **Data Streaming & Export**: Listens for inbound binary DMA frames, converts complex IQ payloads into structured data, and saves them locally as CSV/JSON/Pickle files while rendering plots.

---

## API Submodules

For detailed class and function signatures, select one of the submodules below:

```{eval-rst}
.. rubric:: Core Client & CLI

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   client_package.client.Client
   client_package.prompt_completer

.. rubric:: Protocol & Socket Workers

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   client_package.protocol.Message
   client_package.socket_sender.ThreadedSocketSender
   client_package.socket_reader.ThreadedSocketReader

.. rubric:: Configuration & Preprocessing

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   client_package.yaml_preprocessor

.. rubric:: Plotting & Data Utilities

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   client_package.plotting.plot_2d
   plotter.CommandCompleter
   other.data_fetcher.Message
   other.data_fetcher.ThreadedSocketReader
```