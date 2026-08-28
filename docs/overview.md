# Overview

FIREQ-Client provides the Python interface used to configure, launch, and monitor FIREQ experiments. It acts as the entry point for users who want to interact with the server and execute experiments without directly handling low-level communication details.

## What the client does

The client is responsible for:

- connecting to the FIREQ server,
- sending experiment configuration and control commands,
- loading YAML-based experiment definitions,
- collecting acquisition data from the server,
- storing experiment outputs in structured folders,
- supporting basic plotting and export workflows.

## Typical workflow

A typical session starts with the client connecting to the server, then loading an experiment definition from a YAML file. The configuration is sent to the server, the experiment is executed, and the resulting data are saved locally as PKL files. The same workflow can be used for single-shot experiments as well as swept experiments with multiple parameter values.

## Main concepts

- **Experiment definition**: a YAML file describing the system configuration and the variables to sweep.
- **Server communication**: the client exchanges control and data messages with the runtime server.
- **Experiment output**: each run produces a timestamped folder containing configuration, data, and summary files.
- **Plotting and post-processing**: the client also provides utilities for inspecting captured data through plots, exports, and reduced data representations.
