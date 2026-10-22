"""Client for the FIREQ server.

Connects to the server over TCP, performs the handshake, runs experiments
from YAML configuration files and stores the results on disk.
"""

import itertools
import logging
import os
import shlex
import socket
import time
from queue import Empty

import numpy as np
import pandas as pd
import yaml
from tqdm import tqdm

from .export import export
from .network import Message, ReceiveWorker, SendWorker
from .prompt_completer import make_prompt_session
from .yaml_preprocessor import load_and_resolve

DEFAULT_AUTH_TOKEN = "fireq"
CLIENT_NAME = "minimal_client"
DEFAULT_OUTPUT_DIR = "experiment_output"
#: socket timeout once connected; the workers retry on timeouts, so it only bounds how
#: long a blocked send/recv takes to notice a stop request
SOCKET_TIMEOUT = 5.0
#: a configuration that fails midway leaves the hardware partially configured (server protocol §3)
RESET_HINT = "Send reset_all before applying a configuration again"


class ServerError(Exception):
    """The server answered a command with an error packet."""


# ─── Client ───────────────────────────────────────────────────────
class Client:
    """Interactive client for the FIREQ server."""

    def __init__(
        self,
        host: str,
        port: int,
        auth_token: str = DEFAULT_AUTH_TOKEN,
        output_dir: str = DEFAULT_OUTPUT_DIR,
    ) -> None:
        """Initialize the client with the server address.

        :param host: server hostname or IP address.
        :type host: str
        :param port: server TCP port.
        :type port: int
        :param auth_token: token sent to the server in the handshake.
        :type auth_token: str
        :param output_dir: directory where the experiment data is stored.
        :type output_dir: str
        """
        self.host = host
        self.port = port
        self.auth_token = auth_token
        self.output_dir = output_dir
        self.log = logging.getLogger(__name__)
        self.sock: socket.socket | None = None
        self.reader: ReceiveWorker | None = None
        self.sender: SendWorker | None = None

    def start(self) -> None:
        """Connect, perform the handshake and enter the command loop."""
        connected = False
        while not connected:
            try:
                self._connect()
                connected = True
            except Exception:
                self.log.exception("Caught exception.")
                if input(" retry? [y/n] ").strip().lower() != "y":
                    return
        try:
            self._do_handshake()
            # Create a session with history file
            completer = make_prompt_session()

            self.log.info("Connected. Type commands, 'quit' to exit.")
            while True:
                cmd = completer.prompt("> ").strip()
                if not cmd:
                    continue
                if cmd.lower() in ("quit", "exit"):
                    break
                try:
                    self._dispatch_command(cmd)
                except ConnectionError as err:
                    self.log.error(f"{err}. The session is closed, restart the client to reconnect.")
                    break
                except Exception as err:
                    print(f"{cmd.split()[0]} failed: {type(err).__name__}: {err}")
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            self._disconnect()
            self.log.info("Disconnected.")

    def _connect(self, timeout: float = 1.0) -> bool:
        """Connect to the server and start the network workers.

        :param timeout: timeout of the connection attempt, in seconds.
        :type timeout: float
        :return: True once connected.
        :rtype: bool
        """
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        # the connection timeout would otherwise stay on the socket and interrupt large sends
        self.sock.settimeout(SOCKET_TIMEOUT)
        self.reader = ReceiveWorker(self.sock)
        self.sender = SendWorker(self.sock)
        self.reader.start()
        self.sender.start()
        return True

    def _disconnect(self) -> None:
        """Shut down the reader, the sender and close the socket."""
        workers = [worker for worker in (self.reader, self.sender) if worker]
        # flag the workers first, so the shutdown below is not reported as a lost connection
        for worker in workers:
            worker.stop(timeout=0)
        if self.sock is not None:
            # wake up a reader blocked in recv() now, instead of after the socket timeout
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for worker in workers:
            worker.stop()
        # close the socket
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass

    def _connection_lost(self) -> bool:
        """Tell whether one of the network workers ended because of a connection error.

        :return: True if the connection is lost.
        :rtype: bool
        """
        return bool(
            (self.reader is not None and self.reader.failed.is_set())
            or (self.sender is not None and self.sender.failed.is_set())
        )

    def _drain_queue(self) -> None:
        """Discard the messages left in the reader queue, e.g. by an aborted command."""
        while True:
            try:
                stale = self.reader._queue.get_nowait()
            except Empty:
                return
            self.log.debug(f"Discarded stale message: {stale.header}")

    def _do_handshake(self) -> None:
        """Perform the protocol handshake with the server."""
        # 1. Receive handshake from server
        handshake = self._wait_for_message()
        if handshake.header.get("type") != "handshake":
            raise ValueError(f"Expected handshake, got {handshake.header}")

        ack = {"type": "handshake_ack", "token": self.auth_token, "client_name": CLIENT_NAME}
        self.sender.send(Message(header=ack))
        self.log.info("Handshake completed.")

    def _dispatch_command(self, cmd: str) -> None:
        """Process a user command.

        :param cmd: command line entered by the user.
        :type cmd: str
        """
        cmd_parts = shlex.split(cmd)
        command = cmd_parts[0]
        # replies left over by an aborted command must not be mistaken for this command's reply
        self._drain_queue()
        if command == "ping":
            self._ping()
        elif command == "config_yaml":
            if len(cmd_parts) < 2:
                print("Usage: config_yaml <yaml_file>")
                return
            yaml_file = cmd_parts[1]
            self._config_from_yaml(yaml_file)
        elif command == "run_yaml":
            if len(cmd_parts) < 2:
                print("Usage: run_yaml <yaml_file>")
                return
            yaml_file = cmd_parts[1]
            self._run_yaml(yaml_file)
        elif command == "reset_all":
            self._reset_all()
        elif command == "mts_sync":
            self._mts_sync()
        elif command == "trigger_manually":
            if len(cmd_parts) < 2:
                print("Usage: trigger_manually <generator_IP_name>")
                return
            self._trigger_manually(cmd_parts[1])
        elif command == "set_nyquist":
            if len(cmd_parts) < 4:
                print("Usage: set_nyquist <tile> <block> <zone>")
                return
            self._set_nyquist(int(cmd_parts[1]), int(cmd_parts[2]), int(cmd_parts[3]))
        elif command == "export":
            if len(cmd_parts) < 3:
                print("Usage: export <from_directory> <to_directory>")
            else:
                self._export(cmd_parts[1], cmd_parts[2])
        else:
            print(f"Unknown command: {command}")

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def _trigger_manually(self, ip_name: str) -> None:
        """
        Trigger an IP manually if supported.

        :param ip_name: Name of the IP to be triggered.
        :type ip_name: str
        """
        self.sender.send(Message(header={"cmd": "trigger_manually", "ip_name": ip_name}))
        resp = self._next_reply()
        print(resp.header)

    def _ping(self) -> None:
        """Send a ping to confirm the session is working."""
        ping = {"cmd": "ping"}
        self.sender.send(Message(header=ping))
        response = self._next_reply()
        print("Ping response: ", response.header)

    def _set_nyquist(self, tile: int, block: int, zone: int) -> None:
        """Set the Nyquist zone on the given tile, block and zone.

        :param tile: tile index.
        :type tile: int
        :param block: block index.
        :type block: int
        :param zone: zone index.
        :type zone: int
        """
        nyquist = {"cmd": "set_nyquist", "tile": tile, "block_id": block, "zone": zone}
        self.sender.send(Message(header=nyquist))
        # the server answers every command, so the reply must be read here and not by the next command
        print(self._next_reply().header)

    def _reset_all(self) -> None:
        """Reset the state of the server."""
        reset = {"cmd": "reset_all"}
        self.sender.send(Message(header=reset))
        response = self._next_reply()
        print("Reset all response: ", response.header)

    @staticmethod
    def _load_config(yaml_file: str) -> dict:
        """Load a YAML configuration file and check it describes a system.

        ``variables`` may legitimately be empty (a single-point experiment), but an empty
        ``sys_config`` would be applied to the server as an empty system tree.

        :param yaml_file: path to the YAML configuration file.
        :type yaml_file: str
        :raises ValueError: if the file has no (or an empty) ``sys_config``.
        :return: the resolved configuration, with the ``sys_config`` and ``variables`` keys.
        :rtype: dict
        """
        config = load_and_resolve(yaml_file)
        if not config.get("sys_config"):
            raise ValueError(f"Invalid YAML file {yaml_file}: missing or empty 'sys_config'")
        return config

    def _config_from_yaml(self, yaml_file: str) -> None:
        """
        Load a YAML file and configure the system.

        :param yaml_file: path to the YAML configurtion file.
        :type yaml_file: str
        """
        # load and preprocess the file
        config = self._load_config(yaml_file)
        # send the config to the server
        self.sender.send(
            Message(
                header={
                    "cmd": "apply_configuration",
                    "system": config["sys_config"],
                    "variables": config["variables"],
                }
            )
        )
        try:
            resp = self._next_reply()
        except ServerError as err:
            raise ServerError(f"{err}. {RESET_HINT}") from None
        if not self._is_status(resp, "ok"):
            raise TypeError(f"Unexpected reply to apply_configuration: {resp.header}")
        # sweep expressions are not applied by apply_configuration, and the server says so in a
        # warning that follows the acknowledgement
        if self._has_sweep_expressions(config["sys_config"]):
            warning = self._wait_for_message()
            if warning.header.get("type") == "warning":
                self.log.warning(f"Server warning: {warning.header.get('msg')}")
            else:
                self.log.warning(f"Expected a warning about the sweep expressions, got: {warning.header}")
        print("Configuration applied.")

    def _run_yaml(self, yaml_file: str) -> None:
        """Load a YAML file and run the experiment it describes.

        :param yaml_file: path to the YAML configuration file.
        :type yaml_file: str
        """
        # load and preprocess the file
        config = self._load_config(yaml_file)
        # send the config to the server
        self.sender.send(
            Message(
                header={
                    "cmd": "config_and_run",
                    "system": config["sys_config"],
                    "variables": config["variables"],
                }
            )
        )
        # get the filename
        filename = os.path.basename(yaml_file)  # "file.yaml"
        exp_name = os.path.splitext(filename)[0]  # "file" (without extension)
        self._fetch_experiment(config, exp_name)

    def _mts_sync(self) -> None:
        """Send the mts_sync command to the server."""
        self.sender.send(Message(header={"cmd": "mts_sync"}))
        print(self._next_reply().header)

    def _export(self, from_dir: str, to_dir: str) -> None:
        """Export an experiment (or a tree of experiments) to another directory.

        :param from_dir: source directory.
        :type from_dir: str
        :param to_dir: destination directory.
        :type to_dir: str
        """
        export(from_dir, to_dir)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _fetch_experiment(self, config: dict, experiment_name: str) -> None:
        """Fetch and save the data of an experiment.

        This function will retrieve all dma payloads from the server.

        :param config: resolved YAML configuration.
        :type config: dict
        :param experiment_name: name used for the output folder.
        :type experiment_name: str
        """
        # the server acknowledges the configuration with "status ok" and then sends the experiment
        # header; an error before the header means the run was rejected (no header, no footer follow)
        acknowledged = False
        while True:
            try:
                message = self._next_reply()
            except ServerError as err:
                raise ServerError(f"Run rejected: {err}. {RESET_HINT}") from None
            if not acknowledged and self._is_status(message, "ok"):
                acknowledged = True
            elif self._is_status(message, "experiment_header"):
                break
            else:
                raise TypeError(f"Expected the experiment header from the server, got: {message.header}")

        # the var order is from outer to inner; a single run has neither
        var_order = message.header.get("variable_order", ["none"])
        var_values = message.header.get("variable_values", {"none": np.array([0])})

        # make a points iterator and compute all points (total iterations); only the variables in
        # var_order are swept, variable_values can also hold the unused ones
        points = itertools.product(*[range(len(var_values[v])) for v in var_order])
        total_iterations = 1
        for var in var_order:
            total_iterations *= len(var_values[var])

        # create the directory and save a summary
        try:
            exp_dir = self._make_timestamped_experiment_directory(experiment_name)
            summary = {
                "experiment_name": experiment_name,
                "full_experiment_header": message.header,
                "var_order": var_order,
                "var_values": var_values,
                "expected_iterations": total_iterations,
                "config": config,
            }
            self._save_dict(summary, exp_dir, "experiment_summary")

            # Progress bar over total number of iterations
            pbar = tqdm(total=total_iterations, desc="Fetching")
            try:
                runtimes, run_time, aborted = self._fetch_iterations(exp_dir, points, total_iterations, pbar)
            finally:
                pbar.close()
        except ConnectionError:
            raise
        except Exception:
            # the server keeps streaming the rest of the experiment: read it out, so that the next
            # command does not take it as its own reply
            self._skip_to_footer()
            raise
        self._save_dict({"runtimes": runtimes, "run_time": run_time, "aborted": aborted}, exp_dir, "runtimes")
        if aborted:
            self.log.warning(f"The server aborted the run, the data in {exp_dir} is partial")

    def _skip_to_footer(self) -> None:
        """Discard the messages of the running experiment up to and including its footer.

        The server always sends the footer after the experiment header, also when the run fails.
        """
        self.log.warning("Discarding the rest of the experiment until the server ends it (Ctrl-C closes the session)")
        while True:
            message = self._wait_for_message()
            if self._is_status(message, "experiment_footer"):
                return

    def _fetch_iterations(
        self, exp_dir: str, points: itertools.product, total_iterations: int, pbar: tqdm
    ) -> tuple[list[int], int | None, bool]:
        """Receive the iterations of a running experiment until the experiment footer.

        :param exp_dir: experiment directory.
        :type exp_dir: str
        :param points: iterator over the variable index of each iteration.
        :type points: itertools.product
        :param total_iterations: number of iterations expected.
        :type total_iterations: int
        :param pbar: progress bar, updated once per iteration.
        :type pbar: tqdm
        :return: the per-iteration times (ns), the run time (ns) and the aborted flag of the footer.
        :rtype: tuple[list[int], int | None, bool]
        """
        iter_index = 0
        fetched_shots = 0
        iteration_shots = 0
        per_ip_data = {}
        subfolders = {}
        var_checkpoint = next(points)
        # iteration times reported by the server in iteration_ended, in ns
        runtimes = []
        while True:
            message = self._wait_for_message()
            if message.header.get("type") == "warning":
                self.log.warning(f"Server warning: {message.header.get('msg')}")

            elif message.header.get("type") == "error":
                # the run failed: the server follows with the footer, flagged as aborted
                self.log.error(f"Server error during the run: {message.header.get('msg')}")

            elif message.header.get("type") == "dma_package":
                # create the per-ip dictionary of results
                source = message.header.get("source")
                if source not in per_ip_data.keys():
                    per_ip_data[source] = []
                    subdir = source.replace("/", "")
                    subfolders[source] = self._make_subdirectory(exp_dir, subdir)

                per_ip_data[source].append(self._decode_package(message))
                fetched_shots += message.header.get("shots")
                iteration_shots += message.header.get("shots")

            elif message.header.get("type") == "status":
                if message.header.get("msg") == "iteration_ended":
                    # save data if any was received
                    if iteration_shots > 0:
                        for source, package_list in per_ip_data.items():
                            if not package_list:
                                continue

                            # Concatenate flat arrays once
                            combined_arr = np.concatenate(package_list)

                            # Compute shots per IP source safely and create the df
                            shots_per_ip = iteration_shots // len(per_ip_data)
                            df = self._make_df_from_shots(combined_arr, shots_per_ip)

                            # save the dataframe
                            filename = f"data_{'_'.join(map(str, var_checkpoint))}"
                            self._save_dataframe(df, subfolders[source], filename)

                            # Clear data list for the next iteration
                            per_ip_data[source] = []
                    # record the iteration time, increment iteration index and reset iteration shots
                    runtimes.append(message.header.get("time"))
                    iter_index += 1
                    iteration_shots = 0
                    pbar.update(1)
                    try:
                        var_checkpoint = next(points)
                    except StopIteration:
                        self.log.debug("Caught stop iterator when advancing variables for next point")
                elif message.header.get("msg") == "experiment_footer":
                    self.log.info(
                        f"Experiment ended, fetched {iter_index}/{total_iterations} iterations, "
                        f"{fetched_shots} total shots"
                    )
                    return runtimes, message.header.get("run_time"), bool(message.header.get("aborted", False))
                else:
                    raise ValueError(
                        f"Unexpected status message from server while running experiment: {message.header}"
                    )
            else:
                raise TypeError(f"Unexpected message type from server while running experiment: {message.header}")

    @staticmethod
    def _make_df_from_shots(array: np.ndarray, shots: int) -> pd.DataFrame:
        """Turn the shot array into a DataFrame indexed by shot and time.

        The array must be 1-D. The resulting dataframe will be multi-index and include a
        shot and time column. The time column will be fixed to 0 if the input array length is equal
        to the input number of shots.

        :param array: shot samples.
        :type array: np.ndarray
        :param shots: number of shots in the array.
        :type shots: int
        :return: DataFrame with "shot" and "time" index levels and a "value" column.
        :rtype: pd.DataFrame
        """
        total = len(array)
        points_per_shot = total // shots
        if total % shots != 0:
            raise ValueError(f"Array length ({total}) is not divisible by shots ({shots})")
        data_2d = array.reshape(shots, points_per_shot)

        n_times = data_2d.shape[1]
        index = pd.MultiIndex.from_product(
            [range(shots), range(n_times)],
            names=["shot", "time"],
        )
        return pd.DataFrame({"value": data_2d.ravel()}, index=index)

    def _wait_for_message(self) -> Message:
        """Read the queue until a message is received.

        :raises ConnectionError: if the connection is lost while waiting.
        :return: message received from the server
        :rtype: Message
        """
        while True:
            try:
                response = self.reader._queue.get(timeout=1.0)
            except Empty:
                # only checked once the queue is empty, so messages received before the drop are still served
                if self._connection_lost():
                    raise ConnectionError("Connection to the server lost") from None
                continue
            return response

    def _next_reply(self) -> Message:
        """Wait for the next message that is not a warning; warnings are logged.

        :raises ServerError: if the server answers with an error packet.
        :return: the reply.
        :rtype: Message
        """
        while True:
            message = self._wait_for_message()
            kind = message.header.get("type")
            if kind == "warning":
                self.log.warning(f"Server warning: {message.header.get('msg')}")
            elif kind == "error":
                raise ServerError(message.header.get("msg"))
            else:
                return message

    @staticmethod
    def _is_status(message: Message, msg: str) -> bool:
        """Tell whether a message is the status packet with the given msg.

        :param message: message received from the server.
        :type message: Message
        :param msg: expected value of the msg field.
        :type msg: str
        :return: True if the message is that status packet.
        :rtype: bool
        """
        return message.header.get("type") == "status" and message.header.get("msg") == msg

    @classmethod
    def _has_sweep_expressions(cls, value: object) -> bool:
        """Tell whether a configuration holds sweep expressions, with the server's rule.

        A sweep expression is the string value of a ``$`` key that starts with "#". Values under
        ``_`` keys never count, nor do the items of a list value of a ``$`` key; node maps and
        lists of children are searched recursively.

        :param value: configuration, or part of it.
        :type value: object
        :return: True if a sweep expression is found.
        :rtype: bool
        """
        if isinstance(value, list):
            return any(cls._has_sweep_expressions(item) for item in value)
        if not isinstance(value, dict):
            return False
        for raw_key, item in value.items():
            key = str(raw_key)  # YAML allows non-string keys
            if key.startswith("$"):
                if isinstance(item, str) and item.startswith("#"):
                    return True
            elif not key.startswith("_") and cls._has_sweep_expressions(item):
                return True
        return False

    def _decode_package(self, package: Message) -> np.ndarray:
        """Decode the payload of a DMA package into complex IQ samples.

        :param package: the DMA package to decode.
        :type package: Message
        :return: complex IQ samples.
        :rtype: np.ndarray
        """
        # get dtype, sent over msgpack so it is made of lists
        dt = package.header.get("format")
        dt = [(name, fmt) for name, fmt in dt]
        dt = np.dtype(dt)
        arr = np.frombuffer(package.data, dtype=dt)
        return arr["real"] + 1.0j * arr["imag"]

    def _make_timestamped_experiment_directory(self, dir_name: str) -> str:
        """Create a timestamped directory in the output dir.

        :param dir_name: name of the directory.
        :type dir_name: str
        :return: path of the created directory.
        :rtype: str
        """
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        base_name = f"{self.output_dir}/{dir_name}/experiment_{timestamp}"
        # two runs can start within the same second: suffix the directory until one is free
        candidate = base_name
        index = 1
        while True:
            try:
                os.makedirs(candidate)
                return candidate
            except FileExistsError:
                candidate = f"{base_name}_{index}"
                index += 1

    def _make_subdirectory(self, base_dir: str, subdir_name: str) -> str:
        """
        Make a sub-directory within a base directory.

        :param base_dir: Base directory name
        :type base_dir: str
        :param subdir_name: Name of the subdirectory to be created
        :type subdir_name: str
        :return: Path to the folder
        :rtype: str
        """
        directory = f"{base_dir}/{subdir_name}"
        os.makedirs(directory, exist_ok=True)
        return directory

    def _save_dict(self, d: dict, dir_name: str, file_name: str) -> None:
        """Save a dict as YAML in a directory.

        :param d: dict to save.
        :type d: dict
        :param dir_name: experiment directory.
        :type dir_name: str
        :param file_name: name of the YAML file (without extension).
        :type file_name: str
        """
        with open(os.path.join(dir_name, f"{file_name}.yaml"), "w") as f:
            yaml.dump(d, f, indent=2)

    def _save_dataframe(self, df: pd.DataFrame, dir_name: str, file_name: str) -> None:
        """Save a DataFrame as pickle in the experiment directory.

        :param df: data to save.
        :type df: pd.DataFrame
        :param dir_name: experiment directory.
        :type dir_name: str
        :param file_name: name of the pickle file (without extension).
        :type file_name: str
        """
        # Save DataFrame (can use Parquet or HDF5 for efficiency)
        df.to_pickle(os.path.join(dir_name, f"{file_name}.pkl"))
        df.to_csv(os.path.join(dir_name, f"{file_name}.csv"))
