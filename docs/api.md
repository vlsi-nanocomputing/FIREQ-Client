# Client API

The client implementation is organized around a small set of Python modules that handle communication, parsing, and experiment execution.

## Main components

- `client_package.client.Client`: the main interface used to connect to the server, dispatch commands, and run experiments.
- `client_package.yaml_preprocessor`: resolves YAML placeholders and prepares the experiment configuration before execution.
- `client_package.socket_sender` and `client_package.socket_reader`: manage the low-level communication channel with the server.
- `client_package.plotting`: provides plotting and export helpers for experiment data.

## Main execution flow

The core execution flow is:

1. create a `Client` instance with the target host and port,
2. connect to the server,
3. load a YAML experiment definition,
4. send the configuration to the server,
5. collect the returned data and save it locally.

This API is currently focused on interactive use and experiment execution rather than a full public SDK interface.
