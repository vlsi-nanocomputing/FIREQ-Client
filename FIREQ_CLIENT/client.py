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

AUTH_TOKEN = "fireq"
CLIENT_NAME = "minimal_client"


# ─── Client ───────────────────────────────────────────────────────
class Client:
    """Interactive client for the FIREQ server."""

    def __init__(self, host: str, port: int) -> None:
        """Initialize the client with the server address.

        :param host: server hostname or IP address.
        :type host: str
        :param port: server TCP port.
        :type port: int
        """
        self.host = host
        self.port = port
        self.log = logging.getLogger(__name__)
        self.log.setLevel(logging.INFO)
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
                r = input(" retry? [y/n] ")
                if r:
                    continue
                else:
                    return
        self._do_handshake()
        # Create a session with history file
        completer = make_prompt_session()

        self.log.info("Connected. Type commands (empty function dispatch). 'quit' to exit.")
        try:
            while True:
                cmd = completer.prompt("> ").strip()
                if not cmd:
                    continue
                if cmd.lower() in ("quit", "exit"):
                    break
                self._dispatch_command(cmd)
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            self._disconnect()
            self.log.info("Disconnected.")

    def _connect(self, timeout: float = 1.0) -> bool:
        """Connect to the server and start the network workers."""
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.reader = ReceiveWorker(self.sock)
        self.sender = SendWorker(self.sock)
        self.reader.start()
        self.sender.start()
        return True

    def _disconnect(self) -> None:
        """Shut down the reader, the sender and close the socket."""
        if self.reader:
            self.reader.stop()
        if self.sender:
            self.sender.stop()
        # close the socket
        try:
            self.sock.close()
        except OSError:
            pass

    def _do_handshake(self) -> None:
        """Perform the protocol handshake with the server."""
        # 1. Receive handshake from server
        handshake = self.reader._queue.get()
        if handshake.header.get("type") != "handshake":
            raise ValueError(f"Expected handshake, got {handshake.header}")

        ack = {"type": "handshake_ack", "token": AUTH_TOKEN, "client_name": CLIENT_NAME}
        self.sender.send(Message(header=ack))
        self.log.info("Handshake completed.")

    def _dispatch_command(self, cmd: str) -> None:
        """Process a user command.

        :param cmd: command line entered by the user.
        :type cmd: str
        """
        cmd_parts = shlex.split(cmd)
        command = cmd_parts[0]
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
        resp = self._wait_for_message()
        print(resp.header)

    def _ping(self) -> None:
        """Send a ping to confirm the session is working."""
        ping = {"cmd": "ping", "session_id": "test_session"}
        self.sender.send(Message(header=ping))
        response = self.reader._queue.get()
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

    def _reset_all(self) -> None:
        """Reset the state of the server."""
        reset = {"cmd": "reset_all", "session_id": "test_session"}
        self.sender.send(Message(header=reset))
        response = self.reader._queue.get()
        print("Reset all response: ", response.header)

    def _config_from_yaml(self, yaml_file: str) -> None:
        """
        Load a YAML file and configure the system.

        :param yaml_file: path to the YAML configurtion file.
        :type yaml_file: str
        """
        # load and preprocess the file
        config = load_and_resolve(yaml_file)
        # TODO: maybe do a check here or something
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
        resp = self._wait_for_message()
        print(f"{resp}")

    def _run_yaml(self, yaml_file: str) -> None:
        """Load a YAML file and run the experiment it describes.

        :param yaml_file: path to the YAML configuration file.
        :type yaml_file: str
        """
        # load and preprocess the file
        config = load_and_resolve(yaml_file)
        # check for the existance of keys TODO: why is there a check on variable keys?
        if "sys_config" not in config or "variables" not in config:
            raise ValueError(f"Invalid YAML file: {yaml_file}")
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
        # the first message must be the experiment header
        message = self._wait_for_message()
        if message.header.get("type") == "status":
            pass
        else:
            raise TypeError(f"Unexpected message type from server while running experiment: {message.header}")

        # see if the message is the experiment header, get the var order and variable values
        if message.header.get("msg") == "experiment_header":
            # the var order is from outer to inner
            var_order = message.header.get("variable_order", ["none"])
            var_values = message.header.get("variable_values", {"none": np.array([0])})
        else:
            raise ValueError(f"Unexpected status message from server while running experiment: {message.header}")

        # make a points iterator and compute all points (total iterations)
        points = itertools.product(*[range(len(var_values[v])) for v in var_order])
        total_iterations = 1
        for _, arr in var_values.items():
            total_iterations *= len(arr)

        # create the directory and save a summary
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

        # get iterations until the experiment footer is received
        iter_index = 0
        fetched_shots = 0
        iteration_shots = 0
        per_ip_data = {}
        subfolders = {}
        experiment_stop = False
        var_checkpoint = next(points)
        runtimes = []
        while not experiment_stop:
            message = self._wait_for_message()
            if message.header.get("type") == "dma_package":
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
                    # increment iteration index and reset iteration shots
                    iter_index += 1
                    iteration_shots = 0
                    pbar.update(1)
                    try:
                        var_checkpoint = next(points)
                    except StopIteration:
                        self.log.debug("Caught stop iterator when advancing variables for next point")
                elif message.header.get("msg") == "experiment_footer":
                    experiment_stop = True
                    pbar.close()
                    self.log.info(
                        f"Experiment ended, fetched {iter_index}/{total_iterations} iterations, {fetched_shots} total shots"
                    )
                    self._save_dict(
                        {"runtimes": runtimes, "sweep_total": message.header.get("sweep_time")}, exp_dir, "runtimes"
                    )
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

        :return: message received from the server
        :rtype: Message
        """
        while True:
            try:
                response = self.reader._queue.get(timeout=1.0)
            except Empty:
                continue
            return response

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
        """Create a timestamped directory in the experiment_output dir.

        :param dir_name: name of the directory.
        :type dir_name: str
        :return: path of the created directory.
        :rtype: str
        """
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        base_name = f"experiment_output/{dir_name}/experiment_{timestamp}"
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
        """Save a dict as JSON in a directory.

        :param d: dict to save.
        :type d: dict
        :param dir_name: experiment directory.
        :type dir_name: str
        :param file_name: name of the JSON file (without extension).
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
