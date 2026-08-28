"""Data loading helpers for FIREQ experiment data."""

from pathlib import Path

import numpy as np
import pandas as pd


def load_dataframe(var_order: list[str], var_values_dict: dict[str, np.ndarray], exp_dir: Path) -> pd.DataFrame:
    """Load all saved data files of an experiment and merge them into one DataFrame.

    :param var_order: names of the swept variables.
    :type var_order: list[str]
    :param var_values_dict: values of each swept variable.
    :type var_values_dict: dict[str, np.ndarray]
    :param exp_dir: experiment directory.
    :type exp_dir: Path
    :return: combined DataFrame indexed by the swept variables and time.
    :rtype: pd.DataFrame
    """
    var_checkpoint = [0] * len(var_order)

    def update_checkpoint() -> bool:
        """Increment the var checkpoints; return True while more combinations remain.

        :return: True while more combinations remain, False once all are consumed.
        :rtype: bool
        """
        for i in range(len(var_checkpoint)):
            var_checkpoint[i] += 1
            name = var_order[i]
            if var_checkpoint[i] == len(var_values_dict[name]):
                var_checkpoint[i] = 0
            else:
                return True
        return False

    def filename_checkpoint() -> str:
        """Build the data file name from the current checkpoints.

        :return: file name with the checkpoint values appended.
        :rtype: str
        """
        name = "data"
        for n in var_checkpoint:
            name += f"_{n}"
        return name

    load_data = True
    frames = []  # list of averaged DataFrames
    configs = []  # list of configuration tuples

    while load_data:
        frame_name = filename_checkpoint()
        frame_df = pd.read_pickle(f"{exp_dir}/{frame_name}.pkl")

        # Average over repetitions, keep only the time level
        df_avg = frame_df.groupby(level='time').mean()

        # Get the current var values, index from the checkpoint into the var values
        current_config = tuple(var_checkpoint)
        configs.append(current_config)
        frames.append(df_avg)

        load_data = update_checkpoint()

    # Now build the master DataFrame
    return pd.concat(frames, keys=configs, names=var_order)


def load_dataframe_raw(exp_dir: Path) -> pd.DataFrame:
    """Load the raw data.pkl file of an experiment.

    :param exp_dir: experiment directory.
    :type exp_dir: Path
    :return: the raw DataFrame.
    :rtype: pd.DataFrame
    """
    return pd.read_pickle(f"{exp_dir}/data.pkl")
