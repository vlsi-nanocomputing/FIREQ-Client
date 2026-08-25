"""Client for the FIREQ server.

Connects to the server over TCP, performs the handshake, runs experiments
from YAML configuration files and stores the results on disk.
"""

import json
import logging
import os
import shlex
import socket
import time

import numpy as np
import pandas as pd
from tqdm import tqdm

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

    def connect(self) -> None:
        """Connect to the server and start the network workers."""
        self.sock = socket.create_connection((self.host, self.port))
        self.reader = ReceiveWorker(self.sock)
        self.sender = SendWorker(self.sock)
        self.reader.start()
        self.sender.start()

    def disconnect(self) -> None:
        """Shut down the reader, the sender and close the socket."""
        if self.reader:
            self.reader.stop()
        if self.sender:
            self.sender.stop()
        # In case one of the stops already closed the socket
        try:
            self.sock.close()
        except OSError:
            pass

    # ─── Placeholder functions ───────────────────────────────────

    def run(self) -> None:
        """Connect, perform the handshake and enter the command loop."""
        self.connect()
        self._do_handshake()
        # Create a session with history file
        completer = make_prompt_session()

        print("Connected. Type commands (empty function dispatch). 'quit' to exit.")
        try:
            while True:
                cmd = completer.prompt("> ").strip()
                if not cmd:
                    continue
                if cmd.lower() in ('quit', 'exit'):
                    break
                self._dispatch_command(cmd)
        finally:
            self.disconnect()
            print("Disconnected.")

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
        elif command == "run_yaml":
            yaml_file = cmd_parts[1]
            self._run_yaml(yaml_file)
        elif command == "reset_all":
            self._reset_all()
        elif command == "mts_sync":
            self._mts_sync()
        elif command == "trigger_manually":
            m = {"generator": "/axisGeneratorIP_0"}
            self.sender.send(Message(header=m))
        elif command == "set_nyquist":
            self._set_nyquist(int(cmd_parts[1]), int(cmd_parts[2]), int(cmd_parts[3]))
        else:
            print(f"Unknown command: {command}")

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

    def _run_yaml(self, yaml_file: str) -> None:
        """Load a YAML file and run the experiment it describes.

        :param yaml_file: path to the YAML configuration file.
        :type yaml_file: str
        """
        # load and preprocess the file
        config = load_and_resolve(yaml_file)
        # check for the existance of keys
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
        if config.get("variables"):
            self._fetch_with_variables(config, exp_name)
        else:
            self._fetch_without_variables(config, exp_name)

    def _mts_sync(self) -> None:
        """Send the mts_sync command to the server."""
        self.sender.send(Message(header={"cmd": "mts_sync"}))

    # ------------------------------------------------------------------
    def _fetch_without_variables(self, config: dict, experiment_name: str) -> None:
        """Fetch and save the data of a single (non-swept) experiment.

        :param config: resolved YAML configuration.
        :type config: dict
        :param experiment_name: name used for the output folder.
        :type experiment_name: str
        """
        shots = config["sys_config"]["$shots"]
        try:
            self._wait_for_experiment_start()
            result = self._fetch_shots(shots)
            end_message = self._wait_for_experiment_stop()
            result = self._make_df_from_shots(result[1], config["sys_config"][result[0]]["$output_type"], shots)
            exp_dir = self._make_experiment_folder(experiment_name, config)
            self._save_experiment(result, exp_dir, end_message)

        except Exception as e:
            self.log.error(f"Failed to run experiment {e}")

    def _fetch_shots(self, shots: int, leave_bar: bool = True) -> tuple[str, np.ndarray]:
        """Fetch a single experiment (no sweep). Data may arrive in several DMA packages.

        :param shots: number of shots to fetch.
        :type shots: int
        :param leave_bar: keep the progress bar after completion.
        :type leave_bar: bool
        :return: the source name and the concatenated shot data.
        :rtype: tuple[str, np.ndarray]
        """
        # NOTE: IT ONLY SUPPORTS A SINGLE SOURCE FOR NOW
        collected_data = []
        collected_shots = 0
        pbar = tqdm(total=shots, desc="Fetching shots", leave=leave_bar)
        while collected_shots < shots:
            package = self._wait_for_next_dma()
            package_source = package.header.get("source")
            package_shots = package.header.get("shots")
            package_data = self._decode_package(package)  # 1D array of shot results

            collected_data.append((package_source, package_data))
            collected_shots += package_shots
            pbar.update(package_shots)

        pbar.close()
        source = collected_data[0][0]

        # concatenate all pieces
        return (source, np.concatenate([arr for _, arr in collected_data]))

    @staticmethod
    def _make_df_from_shots(array: np.ndarray, mode: str, shots: int) -> pd.DataFrame:
        """Turn the shot array into a DataFrame indexed by shot and time.

        :param array: raw shot samples.
        :type array: np.ndarray
        :param mode: output mode ("raw", "decimated" or accumulated).
        :type mode: str
        :param shots: number of shots in the array.
        :type shots: int
        :return: DataFrame with "shot" and "time" index levels and a "value" column.
        :rtype: pd.DataFrame
        """
        if mode in ("raw", "decimated"):
            # split the array into shots equal pieces, make the dataframe with the time axis
            total = len(array)
            points_per_shot = total // shots
            if total % shots != 0:
                raise ValueError(
                    f"Array length ({total}) is not divisible by shots ({shots})"
                )
            # reshape to (shots, time_points)
            data_2d = array.reshape(shots, points_per_shot)
        else:  # each item in the array is a shot
            data_2d = array[:shots].reshape(shots, 1)  # each shot -> single value

        n_times = data_2d.shape[1]
        index = pd.MultiIndex.from_product(
            [range(shots), range(n_times)],
            names=["shot", "time"],
        )
        return pd.DataFrame({"value": data_2d.ravel()}, index=index)

    # ------------------------------------------------------------------
    def _fetch_with_variables(self, config: dict, experiment_name: str) -> None:
        """Fetch a swept experiment with multiple variable combinations.

        :param config: resolved YAML configuration.
        :type config: dict
        :param experiment_name: name used for the output folder.
        :type experiment_name: str
        """
        sys_config = config["sys_config"]
        shots = sys_config["$shots"]
        variable_specs = config["variables"]  # dict: name -> {start, end, num, mode}

        # Build variable axes & compute total combinations
        variable_checkpoint = {}
        total_shots = shots
        for var, var_def in variable_specs.items():
            variable_checkpoint[var] = {
                "num": var_def["num"],
                "checkpoint": 0,
            }
            total_shots *= var_def["num"]

        # Wait for the header to get variable order
        var_order = self._wait_for_variable_order()
        if var_order != [n for n in variable_specs.keys()]:
            self.log.warning(f"Variable order mismatch: expected {[n for n in variable_specs.keys()]}, got {var_order}")
            # Use the order from server

        # make the experiment dir
        exp_dir = self._make_experiment_folder(experiment_name, config)
        self._save_dict(config, exp_dir, "configuration")
        self._save_dict({i: name for i, name in enumerate(var_order)}, exp_dir, "var_order")

        # function to update the checkpoints
        def update_checkpoint() -> None:
            """Increment the checkpoint counter, wrapping around at the max."""
            for var in reversed(var_order):
                variable_checkpoint[var]["checkpoint"] += 1
                if variable_checkpoint[var]["checkpoint"] == variable_checkpoint[var]["num"]:
                    variable_checkpoint[var]["checkpoint"] = 0
                else:
                    return

        def make_filename(start: str) -> str:
            """Build the output file name from the current checkpoints.

            :param start: base name of the file.
            :type start: str
            :return: file name with the checkpoint values appended.
            :rtype: str
            """
            name = start
            for var in var_order:
                check = variable_checkpoint[var]["checkpoint"]
                name += f"_{check}"
            return name

        # Progress bar over total number of packages
        pbar = tqdm(total=total_shots, desc="Fetching")

        # Keep reading until we have filled all combinations
        fetched_shots = 0
        while fetched_shots < total_shots:
            self._wait_for_experiment_start()
            source, array = self._fetch_shots(shots, False)
            df = self._make_df_from_shots(array, config["sys_config"][source]["$output_type"], shots)
            self._save_dataframe(df, exp_dir, make_filename("data"))
            response = self._wait_for_experiment_stop()
            self._save_dict(response.header, exp_dir, make_filename("exp"))
            fetched_shots += shots
            pbar.update(shots)
            update_checkpoint()

        pbar.close()
        end_message = self._wait_for_experiment_stop()
        self._save_dict(end_message.header, exp_dir, "end_message")

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
    def _wait_for_variable_order(self) -> list[str]:
        """Read the queue until we get the sweep_experiment_header.

        :return: list of variable names in the order used by the server.
        :rtype: list[str]
        """
        while True:
            response = self.reader._queue.get()
            typeh = response.header.get("type")
            if typeh == "sweep_experiment_header":
                return response.header.get("variables_order", [])
            elif typeh is not None:
                raise Exception(f"Got unexpected message from server: {response.header}")

    def _wait_for_experiment_start(self) -> None:
        """Read the queue until the server reports the experiment start."""
        while True:
            response = self.reader._queue.get()
            typeh = response.header.get("type")
            if typeh == "status":
                self.log.info(f"{response.header}")
                break
            elif typeh is not None:
                raise Exception(f"Got unexpected message from server: {response.header}")

    def _wait_for_experiment_stop(self) -> Message:
        """Read the queue until the server reports the experiment stop.

        :return: the status message.
        :rtype: Message
        """
        while True:
            response = self.reader._queue.get()
            typeh = response.header.get("type")
            if typeh == "status":
                return response
            elif typeh is not None:
                raise Exception(f"Got unexpected message from server: {response.header}")

    def _wait_for_next_dma(self) -> Message:
        """Read the queue until we get a DMA package.

        :return: the DMA package message.
        :rtype: Message
        """
        while True:
            response = self.reader._queue.get()
            typeh = response.header.get("type")
            if typeh == "dma_package":
                return response
            elif typeh is not None:
                raise Exception(f"Got unexpected message from server: {response.header}")

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
        arr = np.frombuffer(package.payload, dtype=dt)
        return arr["real"] + 1.0j * arr["imag"]

    def _make_experiment_folder(self, experiment_name: str, config: dict) -> str:
        """Create a timestamped experiment directory and save the config in it.

        :param experiment_name: name of the experiment.
        :type experiment_name: str
        :param config: experiment configuration to save as config.json.
        :type config: dict
        :return: path of the created directory.
        :rtype: str
        """
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        dir_name = f"experiment_output/{experiment_name}/experiment_{timestamp}"
        os.makedirs(dir_name, exist_ok=False)
        with open(os.path.join(dir_name, "config.json"), "w") as f:
            json.dump(config, f, indent=2, default=str)
        return dir_name

    def _save_experiment(self, df: pd.DataFrame, dir_name: str, end_message: Message) -> None:
        """Save the experiment data (CSV + pickle) and the end message.

        :param df: experiment data to save.
        :type df: pd.DataFrame
        :param dir_name: experiment directory.
        :type dir_name: str
        :param end_message: server message with the experiment summary.
        :type end_message: Message
        """
        # Save DataFrame (can use Parquet or HDF5 for efficiency)
        df.to_csv(os.path.join(dir_name, "data.csv"), encoding='utf-8')
        df.to_pickle(os.path.join(dir_name, "data.pkl"))
        with open(os.path.join(dir_name, "end_message.json"), "w") as f:
            json.dump(end_message.header, f, indent=2, default=str)

    def _save_dict(self, d: dict, dir_name: str, file_name: str) -> None:
        """Save a dict as JSON in the experiment directory.

        :param d: dict to save.
        :type d: dict
        :param dir_name: experiment directory.
        :type dir_name: str
        :param file_name: name of the JSON file (without extension).
        :type file_name: str
        """
        with open(os.path.join(dir_name, f"{file_name}.json"), "w") as f:
            json.dump(d, f, indent=2, default=str)

    def _save_dataframe(self, df: pd.DataFrame, dir_name: str, file_name: str) -> None:
        """Save a DataFrame as CSV and pickle in the experiment directory.

        :param df: data to save.
        :type df: pd.DataFrame
        :param dir_name: experiment directory.
        :type dir_name: str
        :param file_name: base name of the output files (without extension).
        :type file_name: str
        """
        # Save DataFrame (can use Parquet or HDF5 for efficiency)
        df.to_csv(os.path.join(dir_name, f"{file_name}.csv"), encoding='utf-8')
        df.to_pickle(os.path.join(dir_name, f"{file_name}.pkl"))
