# Output structure

Each experiment run creates a timestamped output directory under:

```text
experiment_output/<experiment_name>/experiment_<timestamp>/
```

The folder is created automatically, named after the moment the run starts. Inside it, the
client stores the experiment under the experiment name and writes the files produced during
acquisition.

Every run writes:

- `experiment_summary.yaml`: the experiment name, the configuration used for the run, and
  the variable information — the sweep variables in `var_order` and their values in
  `var_values`,
- `runtimes.yaml`: the timing of the sweep, written when the experiment ends. It currently
  holds `sweep_total`, the total sweep time reported by the server; the per-iteration
  `runtimes` list is not populated yet,
- one subdirectory per acquisition IP (`axisAcquisitionIP_0/`, `axisAcquisitionIP_1/`, …),
  holding the data acquired by that IP.

The data files inside an acquisition IP subdirectory are named after the index of each
acquisition point in the sweep. For a non-swept experiment there is a single acquisition
point per IP; for a swept experiment there is one file per sweep step, and the numbers in
the file name are the indices of the sweep variables, in `var_order` order. A pickle holds
the data, and the `.csv` next to it holds the same data in text form.

If the experiment contains a single sweep variable with 100 values, the output contains
files such as:

- `data_0.pkl`, `data_0.csv`
- ...
- `data_99.pkl`, `data_99.csv`

If the experiment contains two sweep variables, such as gain and frequency, the output
uses a two-index naming convention such as:

- `data_0_0.pkl`, `data_0_0.csv`
- ...
- `data_99_99.pkl`, `data_99_99.csv`

The complete layout of an experiment swept over one variable looks like:

```text
experiment_output/drive_loopback/experiment_20260914_161330/
├── axisAcquisitionIP_0/
│   ├── data_0.pkl
│   ├── data_0.csv
│   ├── ...
├── axisAcquisitionIP_1/
│   ├── data_0.pkl
│   ├── data_0.csv
│   └── ...
├── experiment_summary.yaml
└── runtimes.yaml
```

The numbering reflects the sweep index associated with each variable. Together with the
`var_order` recorded in `experiment_summary.yaml`, each index maps back to the
corresponding parameter. To plot an experiment, export it first: the `export` command
combines the per-sweep-point files into one dataframe per acquisition IP.
