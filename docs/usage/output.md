# Output structure

Each experiment run creates a timestamped output directory under:

```text
experiment_output/<experiment_name>/experiment_<timestamp>/
```

The folder is created automatically if it does not exist. Inside it, the client stores the experiment under the experiment name and writes the files produced during acquisition.

For a non-swept experiment, the output typically includes:

- `config.json`: the full configuration used for the run,
- `data.pkl`: the main data output,
- `end_message.json`: summary information reported by the server.

For swept experiments, the client writes one pickle data file per sweep step (for
example, `data_0.pkl`), a per-step status file (for example, `exp_0.json`),
`configuration.json`, `var_order.json`, and `end_message.json`. The swept
experiment also contains the initial `config.json` created for the experiment.

If the experiment contains a single sweep variable with 100 values, the output contains files such as:

- `data_0.pkl`, `exp_0.json`
- ...
- `data_99.pkl`, `exp_99.json`

If the experiment contains two sweep variables, such as gain and frequency, the output uses a two-index naming convention such as:

- `data_0_0.pkl`, `exp_0_0.json`
- ...
- `data_99_99.pkl`, `exp_99_99.json`

The numbering reflects the sweep index associated with each variable. The file `var_order.json` records the order of the sweep variables so that each index can be mapped back to the corresponding parameter.
