import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider
from pathlib import Path
import json
import pandas as pd
import os
import shutil
from matplotlib.colors import Normalize

TIME_MULTIPLIER ={
    "raw" : 0.4283168859,
    "decimated": 1.7132675438
}



def load_dataframe(var_order, var_values_dict, exp_dir) -> pd.DataFrame:
    
    var_checkpoint = [0]*len(var_order)
    def update_checkpoint():
        for i in range(len(var_checkpoint)):
            var_checkpoint[i] +=1
            name = var_order[i]
            if var_checkpoint[i] == len(var_values_dict[name]):
                var_checkpoint[i] = 0
            else:
                return True
        return False
    
    def filename_checkpoint():
        name = "data"
        for n in var_checkpoint:
            name += f"_{n}"
        return name
    
    load_data = True
    frames = []          # list of averaged DataFrames
    configs = []      # list of configuration tuples

    load_data = True
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

def load_dataframe_raw(exp_dir):
    return pd.read_pickle(f"{exp_dir}/data.pkl")


def _plot_2d(
    exp_dir: str,
    plot_magnitude: bool = True,
    plot_imag: bool = False,
    plot_real: bool = False,
    save: bool = False
) -> None:
    """Interactive 2D plot of the experiment data."""

    exp_path = Path(exp_dir)

    # Load experiment configuration
    with open(exp_path / "config.json") as f:
        config = json.load(f)

    with open(exp_path / "var_order.json") as f:
        var_order = json.load(f)

    output_type = config["sys_config"]["/axisAcquisitionIP_0"]["$output_type"]

    var_names = [var_order[str(i)] for i in range(len(var_order))]
    var_values = {
        name: np.linspace(
            config["variables"][name]["start"],
            config["variables"][name]["stop"],
            config["variables"][name]["num"],
        )
        for name in var_names
    }

    dataframe = load_dataframe(var_names, var_values, exp_path)

    if output_type in ("raw", "decimated"):
        xlabel = "Time"
        slider_vars = var_names
    else:
        xlabel = var_names[0]
        slider_vars = var_names[1:]

    plt.style.use("seaborn-v0_8-darkgrid")

    fig, ax = plt.subplots(figsize=(8, 5))
    plt.subplots_adjust(bottom=0.08 * max(2, len(slider_vars) + 1))

    lines = {}

    if plot_real:
        lines["real"], = ax.plot([], [], lw=2, label="Real")

    if plot_imag:
        lines["imag"], = ax.plot([], [], "--", lw=2, label="Imag")

    if plot_magnitude:
        lines["mag"], = ax.plot([], [], ":", lw=2, label="Magnitude")

    ax.legend()
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Amplitude")
    ax.set_title(exp_path.name)

    sliders = {}

    def formatter(var):
        return lambda x: f"{var_values[var][int(x)]:.3f}"

    def update(_=None):

        df = dataframe

        # Apply slider selections
        for var in slider_vars:
            idx = int(sliders[var].val)
            df = df[df.index.get_level_values(var) == idx]

        if df.empty:
            return

        if output_type in ("raw", "decimated"):

            x = (
                df.index.get_level_values("time").to_numpy()
                * TIME_MULTIPLIER[output_type]
            )
            y = df["value"].to_numpy()

        else:
            # REAL sweep values instead of integer indices
            x = var_values[var_names[0]]

            y = df["value"].to_numpy()

        if "real" in lines:
            lines["real"].set_data(x, np.real(y))

        if "imag" in lines:
            lines["imag"].set_data(x, np.imag(y))

        if "mag" in lines:
            lines["mag"].set_data(x, np.abs(y))

        ax.relim()
        ax.autoscale_view()
        fig.canvas.draw_idle()

    bottom = 0.08

    for i, var in enumerate(slider_vars):

        sax = plt.axes([0.15, bottom + i * 0.05, 0.7, 0.03])

        sliders[var] = Slider(
            sax,
            var,
            0,
            len(var_values[var]) - 1,
            valinit=0,
            valstep=1,
            valfmt=formatter(var),
        )

        sliders[var].on_changed(update)

    update()
    if save:
        plt.savefig(exp_path / "a_figure.png")
    plt.show()

def _plot_3d_heatmap(
    exp_dir: str,
    plot_magnitude: bool = True,
    plot_phase: bool = False,
    plot_real: bool = False,
    plot_imag: bool = False,
    save: bool = False
):
    """
    Interactive heatmap.

    accumulated:
        x = variable 0
        y = variable 1

    raw/decimated:
        x = time
        y = variable 0

    Remaining variables become sliders.
    """

    exp_path = Path(exp_dir)

    with open(exp_path / "config.json") as f:
        config = json.load(f)

    with open(exp_path / "var_order.json") as f:
        var_order = json.load(f)

    output_type = config["sys_config"]["/axisAcquisitionIP_0"]["$output_type"]
    time_mult = TIME_MULTIPLIER.get(output_type, 1)

    var_names = [var_order[str(i)] for i in range(len(var_order))]

    var_values = {
        name: np.linspace(
            config["variables"][name]["start"],
            config["variables"][name]["stop"],
            config["variables"][name]["num"],
        )
        for name in var_names
    }

    dataframe = load_dataframe(var_names, var_values, exp_path)

    if output_type in ("raw", "decimated"):
        x_name = "time"
        y_name = var_names[0]
        slider_vars = var_names[1:]
    else:
        x_name = var_names[0]
        y_name = var_names[1]
        slider_vars = var_names[2:]

    mode = (
        "magnitude"
        if plot_magnitude
        else "phase"
        if plot_phase
        else "real"
        if plot_real
        else "imag"
    )

    plt.style.use("seaborn-v0_8-darkgrid")

    fig, ax = plt.subplots(figsize=(8, 6))
    plt.subplots_adjust(bottom=0.08 * max(1, len(slider_vars) + 1))

    img = ax.imshow(
        np.zeros((2, 2)),
        origin="lower",
        aspect="auto",
        interpolation="nearest",
    )

    cbar = plt.colorbar(img, ax=ax)
    cbar.set_label(mode)

    sliders = {}

    def formatter(var):
        return lambda x: f"{var_values[var][int(x)]:.3f}"

    current = {v: 0 for v in slider_vars}

    def update(_=None):

        df = dataframe

        for var in slider_vars:
            idx = int(sliders[var].val)
            current[var] = idx
            df = df[df.index.get_level_values(var) == idx]

        if df.empty:
            return

        if output_type in ("raw", "decimated"):

            heat = (
                df["value"]
                .unstack("time")
                .reindex(range(len(var_values[y_name])))
            )

            x = heat.columns.to_numpy() * time_mult
            y = var_values[y_name]

        else:

            heat = (
                df["value"]
                .droplevel("time")
                .unstack(y_name)
                .reindex(range(len(var_values[x_name])))
            )

            heat = heat.T

            x = var_values[x_name]
            y = var_values[y_name]

        data = heat.to_numpy()

        if mode == "magnitude":
            data = np.abs(data)
            cmap = "viridis"

        elif mode == "phase":
            data = np.angle(data)
            cmap = "twilight"

        elif mode == "real":
            data = np.real(data)
            cmap = "RdBu_r"

        else:
            data = np.imag(data)
            cmap = "RdBu_r"

        img.set_data(data)
        img.set_cmap(cmap)
        img.set_extent([x[0], x[-1], y[0], y[-1]])

        img.set_clim(np.nanmin(data), np.nanmax(data))

        ax.set_xlabel(x_name)
        ax.set_ylabel(y_name)

        fig.canvas.draw_idle()

    bottom = 0.08

    for i, var in enumerate(slider_vars):

        sax = plt.axes([0.15, bottom + i * 0.05, 0.7, 0.03])

        sliders[var] = Slider(
            sax,
            var,
            0,
            len(var_values[var]) - 1,
            valinit=0,
            valstep=1,
            valfmt=formatter(var),
        )

        sliders[var].on_changed(update)

    update()
    if save:
        plt.savefig(exp_path / "a_figure.png")
    plt.show()

def _plot_iq(exp_0, exp_1, save=False):
    exp_path_0 = Path(exp_0)
    exp_path_1 = Path(exp_1)

    # Load experiment configuration
    with open(exp_path_0 / "config.json") as f:
        config_0 = json.load(f)
    with open(exp_path_1 / "config.json") as f:
        config_1 = json.load(f)

    for config in [config_0, config_1]:
        if config["variables"] or config["sys_config"]["/axisAcquisitionIP_0"]["$output_type"] != "accumulated":
            print("Experiment config contains variables or the value is not accumulated")
            return 
    
    # load dataframes
    df_0 = load_dataframe_raw(exp_path_0)
    df_1 = load_dataframe_raw(exp_path_1)

    # take a training dataset which is half the size of the data and 
    # use it to train a linear separation
    #sample_s = df_0["value"].sample(frac=1, random_state=42)
    #mask = df_0["value"].index.isin(sample_s.index)
    #series_mask = pd.Series(mask, index=df_0["value"].index)
    iq_0 : np.ndarray = df_0["value"].to_numpy()
    iq_1 : np.ndarray = df_1["value"].to_numpy()
    plt.scatter(iq_0.real, iq_0.imag, s=5)
    plt.scatter(iq_1.real, iq_1.imag, s=5)
    if save:
        plt.savefig(exp_path_0 / "a_figure.png")
    plt.show()

    # define the mean value for 0 and 1
    mean_0 = iq_0.mean()
    mean_1 = iq_1.mean()

    # find the middle point between the two means
    mid_point = (mean_0 + mean_1) / 2

    # rotate around the middle point and move back
    # Calcoliamo l'angolo della retta che unisce i due punti medi
    angle = np.angle(mean_1 - mean_0)

    # Ruotiamo i dati portando la retta di giunzione parallela all'asse X
    # Usiamo i numeri complessi: (z - centro) * e^(-i * angle) + centro
    iq_0_rot : np.ndarray = (iq_0 - mid_point) * np.exp(-1j * angle) + mid_point
    iq_1_rot : np.ndarray = (iq_1 - mid_point) * np.exp(-1j * angle) + mid_point

    mean_0_rot = (mean_0 - mid_point) * np.exp(-1j * angle) + mid_point
    mean_1_rot = (mean_1 - mid_point) * np.exp(-1j * angle) + mid_point

    # plot a scatter to show the data, show the two means and a line connecting them
    plt.figure(figsize=(10, 5))
    plt.subplot(1, 2, 1)
    plt.scatter(iq_0_rot.real, iq_0_rot.imag, s=5, alpha=0.5, label='Rotated 0')
    plt.scatter(iq_1_rot.real, iq_1_rot.imag, s=5, alpha=0.5, label='Rotated 1')
    plt.plot([mean_0_rot.real, mean_1_rot.real], [mean_0_rot.imag, mean_1_rot.imag], color='red', marker='o', label='Means Line')
    plt.title("Rotated IQ Plane")
    plt.xlabel("I (Real)")
    plt.ylabel("Q (Imag)")
    plt.legend()
    plt.grid(True)

    # consider only the values on the x axis, create cumulative distribution for both, plot the two cumulative distributions
    plt.subplot(1, 2, 2)
    # Estraiamo solo la componente X (reale) dopo la rotazione
    x_0 = iq_0_rot.real
    x_1 = iq_1_rot.real

    # get the limits of the plot
    x_max = max(x_0.max(),x_1.max())
    x_min = min(x_0.min(),x_1.min())

    # create a linspace for the x axis of the CDF
    # 1. Sort your input arrays first (required for searchsorted)
    x_0_sorted = np.sort(x_0)
    x_1_sorted = np.sort(x_1)

    # 2. Create your grid
    x_cdf = np.linspace(x_min, x_max, endpoint=True, num=(x_0.shape[0] + x_1.shape[0]))

    # 3. Use searchsorted to find how many elements are strictly less than each point in x_cdf
    # 'left' ensures we find the index where x_0_sorted < x_cdf[i]
    x_0_cdf = np.searchsorted(x_0_sorted, x_cdf, side='left')
    x_1_cdf = np.searchsorted(x_1_sorted, x_cdf, side='left')
    # normalize to 1
    x_0_cdf = x_0_cdf / x_0.shape[0]
    x_1_cdf = x_1_cdf / x_1.shape[0]

    # find where the two distances are maximized
    x_cdf_abs_diff = np.abs(x_0_cdf - x_1_cdf)
    treshold = np.argmax(x_cdf_abs_diff)
    fidelity = x_cdf_abs_diff[treshold]

    print(f"maximum fidelity with treshold: {treshold} is equal to: {fidelity:.2f}%")

    # Generiamo le distribuzioni cumulative (CDF)
    plt.plot(x_cdf, x_0_cdf, label='CDF 0', linewidth=1.5)
    plt.plot(x_cdf, x_1_cdf, label='CDF 1', linewidth=1.5)
    plt.plot([x_cdf[treshold]]*2, [x_0_cdf[treshold], x_1_cdf[treshold]], color='red', marker='o', label=f"Point of maximum fidelity: {fidelity:.2f}%")
    plt.title("Cumulative Distribution (X-axis)")
    plt.xlabel("Projected X value")
    plt.ylabel("Probability")
    plt.legend()
    plt.grid(True)
    
    plt.tight_layout()
    if save:
        plt.savefig(exp_path_0 / "rotated_analysis.png")
    plt.show()
    plt.clf()

    # find the middle point between the two means

    # rotate around the middle point and move back

    # plot a scatter to show the data, show the two means and a line connecting them

    # consider only the values on the x axis, create comulative distribution for both, plot the two comulative distributions

def _plot_spectr(exp_0, exp_1, save=False):
    exp_path_0 = Path(exp_0)
    exp_path_1 = Path(exp_1)

    # Load experiment configuration
    with open(exp_path_0 / "config.json") as f:
        config_0 = json.load(f)
    with open(exp_path_1 / "config.json") as f:
        config_1 = json.load(f)
    with open(exp_path_0 / "var_order.json") as f:
        var_order_0 = json.load(f)
    with open(exp_path_1 / "var_order.json") as f:
        var_order_1 = json.load(f)
    
    if var_order_1 != var_order_0 or config_0["variables"] != config_1["variables"]:
        print("error, mismatch in variable configuration or var order")
    
    for config in [config_0, config_1]:
        if config["sys_config"]["/axisAcquisitionIP_0"]["$output_type"] != "accumulated":
            print("Experiment config contains variables or the value is not accumulated")
            return 

    var_names = [var_order_0[str(i)] for i in range(len(var_order_0))]
    var_values = {
        name: np.linspace(
            config_0["variables"][name]["start"],
            config_0["variables"][name]["stop"],
            config_0["variables"][name]["num"],
        )
        for name in var_names
    }

    # load dataframes
    df_0 = load_dataframe(var_names, var_values, exp_path_0)
    df_1 = load_dataframe(var_names, var_values, exp_path_1)

    # take the values
    iq_0 : np.ndarray = df_0["value"].to_numpy()
    iq_1 : np.ndarray = df_1["value"].to_numpy()

    # compute the point by point distance, take the maximum
    iq_distance = np.abs(iq_0 - iq_1)
    maxarg = np.argmax(iq_distance)
    
    plt.figure(figsize=(10, 5))
    plt.subplot(1, 2, 1)
    plt.scatter(iq_0.real, iq_0.imag, s=5)
    plt.scatter(iq_1.real, iq_1.imag, s=5)
    # plot the point with maximum distance
    frequency = var_values[var_names[0]]
    best_f = frequency[maxarg]
    plt.plot([iq_0[maxarg].real, iq_1[maxarg].real], [iq_0[maxarg].imag, iq_1[maxarg].imag], color='red', marker='o', label=f"Frequency of maximal distance: {best_f:.4f}")
    plt.title("Spectroscopy based frequency optimization")
    plt.xlabel("I values")
    plt.ylabel("Q values")
    plt.legend()

    # plot the two spectroscopies
    plt.subplot(1, 2, 2)
    plt.plot(frequency, np.abs(iq_0), label= "Response at 0")
    plt.plot(frequency, np.abs(iq_1), label= "Response at 1")
    #plt.hlines(frequency[maxarg],0,max(np.abs(iq_0).max(), np.abs(iq_1).max()))
    plt.tight_layout()
    if save:
        plt.savefig(exp_path_0 / "a_figure.png")
    plt.show()




def export(from_dir, to_dir: Path):
    from_dir = Path(from_dir)
    to_dir = Path(to_dir)
    if from_dir.name.startswith("experiment_") and from_dir.name != "experiment_output":
        export_experiment(from_dir, to_dir)
    else:
        for dir in from_dir.iterdir():
            if dir.is_dir():
                export(dir, to_dir / dir.name)

def export_experiment(from_dir: Path, to_dir: Path):
    print(f"exporting {from_dir}   to   {to_dir} ......")
    os.makedirs(to_dir)

    # Load experiment configuration
    try:
        with open(from_dir / "config.json") as f:
            config = json.load(f)
    except FileNotFoundError:
        # no config = no experiment
        return

    # Load experiment configuration
    var_order = None
    try:
        with open(from_dir / "var_order.json") as f:
            var_order = json.load(f)
    except FileNotFoundError:
        pass

    if var_order is not None:
        var_names = [var_order[str(i)] for i in range(len(var_order))]
        var_values = {
            name: np.linspace(
                config["variables"][name]["start"],
                config["variables"][name]["stop"],
                config["variables"][name]["num"],
            )
            for name in var_names
        }
        df = load_dataframe(var_names, var_values, from_dir)
    else:
        df = load_dataframe_raw(from_dir)

    # save the entire dataframe, plus the config and end_message.json
    df.to_pickle(to_dir / "data.pkl")
    shutil.copy(from_dir / "config.json", to_dir / "config.json")
    if os.path.exists(from_dir / "end_message.json"):
        shutil.copy(from_dir / "end_message.json", to_dir / "end_message.json")
    if os.path.exists(from_dir / "a_figure.png"):
        shutil.copy(from_dir / "a_figure.png", to_dir / "a_figure.png")
    
