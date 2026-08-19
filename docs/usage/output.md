# Output structure

Each experiment run creates a timestamped output directory under:

```text
experiment_output/<experiment_name>/experiment_<timestamp>/
```

The folder is created automatically if it does not exist. Inside it, the client stores the experiment under the experiment name and writes the files produced during acquisition.

For a non-swept experiment, the output typically includes:

- `config.json`: the full configuration used for the run,
- `data.csv` and `data.pkl`: the main data outputs,
- `end_message.json`: summary information reported by the server.

For swept experiments, the client writes one data file pair per sweep step using a suffix of sweep indices starting at 0 (e.g., `data_0.csv`, `data_0.pkl`). A per-step status file is also written as `exp_0.json`.

If the experiment contains a single sweep variable with 100 values, the output contains files such as:

- `data_0.csv`, `data_0.pkl`, `exp_0.json`
- ...
- `data_99.csv`, `data_99.pkl`, `exp_99.json`

If the experiment contains two sweep variables, such as gain and frequency, the output uses a two-index naming convention such as:

- `data_0_0.csv`, `data_0_0.pkl`, `exp_0_0.json`
- ...
- `data_99_99.csv`, `data_99_99.pkl`, `exp_99_99.json`

The numbering reflects the sweep index associated with each variable. The file `var_order.json` records the order of the sweep variables so that each index can be mapped back to the corresponding parameter.
