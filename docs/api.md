# FIREQ Client Architecture & Reference

Welcome to the `FIREQ-Client` API reference and architecture guide. The client acts as the front-end workstation interface that connects to the `FIREQ-Server`, processes experiment definitions, manages TCP streams, and visualizes acquisition results.

---

## Architectural Overview

The client framework is organized into three main operational layers:

1. **Connection Layer**: Establishes and manages TCP socket sessions with the server.
2. **Protocol & Networking Layer**: Implements length-prefixed binary framing and manages background I/O threads.
3. **Experiment & Processing Layer**: Resolves YAML templates, reconstructs raw binary DMA acquisition payloads into numerical arrays, and stores experiment data.

---

## Interactive & Runtime Lifecycle

A standard client runtime lifecycle proceeds as follows:

1. **Instantiation & Connection**: The `Client` instance connects to the specified `<SERVER_IP>:<SERVER_PORT>` endpoint.
2. **Handshake & Auth**: Performs authentication via shared token validation.
3. **YAML Preprocessing**: Loads `.yaml` experiment definitions and resolves internal variable placeholders.
4. **Command Execution**: Dispatches execution requests.
5. **Data Streaming & Export**: Listens for inbound binary DMA frames, converts complex IQ payloads into structured data, and saves them locally as YAML, pickle and CSV files. The client `export` command writes, per acquisition IP, a `data_<acquisition_ip_name>.pkl` dataframe and its matching `.csv` into the target directory, next to a copy of `experiment_summary.yaml`. This is the directory layout consumed by the separate `FIREQ_PLOTTER` package.

---

## API Submodules

For detailed class and function signatures, select one of the submodules below:

```{eval-rst}
.. rubric:: Core Client & CLI

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   FIREQ_CLIENT.client.Client
   FIREQ_CLIENT.prompt_completer

.. rubric:: Protocol & Socket Workers

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   FIREQ_CLIENT.network.protocol.Message
   FIREQ_CLIENT.network.send_worker.SendWorker
   FIREQ_CLIENT.network.receive_worker.ReceiveWorker

.. rubric:: Configuration & Preprocessing

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   FIREQ_CLIENT.yaml_preprocessor

.. rubric:: Data & Export Utilities

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   FIREQ_CLIENT.export

.. rubric:: Plotting

.. autosummary::
   :toctree: _autosummary
   :nosignatures:

   FIREQ_PLOTTER.plotter.CommandCompleter
   FIREQ_PLOTTER.plotter.main
   FIREQ_PLOTTER.plotting.common.load_and_plot
   FIREQ_PLOTTER.plotting.plot_2d._plot_2d
   FIREQ_PLOTTER.plotting.plot_3d_heatmap._plot_3d_heatmap
   FIREQ_PLOTTER.plotting.plot_iq._plot_iq_curve
   FIREQ_PLOTTER.plotting.plot_iq._plot_rotated_cdf
```