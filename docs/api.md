# Client API

This page describes how the FIREQ-Client works as a client-side interface, focusing on its runtime behavior and interaction model.

## Scope

FIREQ-Client is a thin Python client used to connect to the FIREQ server, send experiment commands, receive results, and save them locally. Its role is mostly operational: it acts as the front-end that talks to the server and orchestrates experiment execution.

## Main entry point

The main public object is `Client`, defined in `client_package.client`.

A typical usage pattern is:

```python
from client_package.client import Client

SERVER_IP = "server_ip"
SERVER_PORT = 9091

if __name__ == "__main__":
    # Replace with actual server IP and port
    client = Client(SERVER_IP, SERVER_PORT)
    client.run()
```

In the current launcher, the connection target is configured in `run_client.py` through:

- `SERVER_IP`
- `SERVER_PORT`

To point the client to a different server, change those values or instantiate `Client` with a different host and port.

## Connection model

The client uses a TCP socket to communicate with the server.

### Addressing and configuration

- the client connects to a host/port pair;
- the host is usually a server name or IP address;
- the port is the TCP port exposed by the FIREQ server.

This is the only network endpoint the client needs to know about at startup.

### Framing and message format

Messages are packaged in a small framing layer:

- a 4-byte length prefix is written first;
- the message header is serialized with `msgpack`;
- an optional payload can follow for binary experiment data.

The message object used by the client is `Message`, defined in `client_package.protocol`.

## Multi-threaded behavior

The client is multi-threaded at the networking layer.

It uses two background workers:

- `ThreadedSocketSender`: sends outgoing messages from a queue;
- `ThreadedSocketReader`: reads incoming messages from the socket and places them into a queue.

This design allows the client to keep sending commands and receiving responses without blocking the main execution flow. In practice, the client is not using a complex async architecture, but it does rely on background threads for network I/O.

## Runtime lifecycle

A typical client lifecycle is:

1. create a `Client` instance;
2. call `connect()` to open the socket and start the reader/sender threads;
3. perform the server handshake;
4. issue commands such as ping, reset, or YAML-based experiment execution;
5. disconnect cleanly when done.

## Command and experiment flow

The client supports an interactive loop via `run()`, but the underlying execution logic is organized around a few conceptual steps:

1. prepare the experiment configuration from YAML;
2. send a command to the server;
3. wait for server status messages;
4. receive DMA data packages for the experiment output;
5. decode the binary payload into numerical arrays;
6. save the results locally as CSV, pickle, or JSON files.

For YAML-based runs, the preprocessing step is handled by `client_package.yaml_preprocessor`, which resolves placeholders such as `%variable_name` before the configuration is sent to the server.

## Module overview

| Module | Responsibility |
| --- | --- |
| `client_package.client` | Main client class, connection management, command dispatch, experiment orchestration |
| `client_package.protocol` | Defines the `Message` container used for framed communication |
| `client_package.socket_sender` | Background sender thread and outgoing message queue |
| `client_package.socket_reader` | Background receiver thread and incoming message queue |
| `client_package.yaml_preprocessor` | Loads YAML definitions and resolves placeholders |
| `client_package.plotting` | Plotting and export helpers for experiment outputs |

## Conceptual API structure

From a documentation perspective, the client can be described in three layers:

### 1. Connection layer

Responsible for establishing and maintaining the TCP connection to the server.

- `Client.connect()`
- `Client.disconnect()`

### 2. Protocol layer

Responsible for formatting and exchanging messages with the server.

- `Message.to_buffers()`
- the background sender/reader threads

### 3. Experiment layer

Responsible for turning user commands or YAML definitions into server-side execution and local result processing.

- `Client.run()`
- `Client._run_yaml()`
- `Client._fetch_shots()`
- `Client._make_df_from_shots()`

## Notes on current design

This API is currently designed as an interactive experiment client rather than as a fully polished public SDK.
