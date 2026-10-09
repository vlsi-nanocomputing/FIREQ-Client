"""Run a FIREQ_PLOTTER function in its own process (started by the results browser).

Usage: ``python -m FIREQ_GUI.tools.plot_runner '<json>'`` with
``{"function": "_plot_2d", "dirs": [...], "export_to": [...], "kwargs": {...}}``.
``export_to[i]`` (or null) is where ``dirs[i]`` is exported before plotting when it does not
contain ``data.pkl`` yet. The plot windows block this process, not the GUI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def run(request: dict) -> int:
    """Export the experiments if needed, then call the plotting function.

    :param request: ``function``, ``dirs``, ``export_to`` and ``kwargs`` (see the module docstring)
    :type request: dict
    :return: the process exit code.
    :rtype: int
    """
    # heavy imports deferred to the child process that actually plots
    from FIREQ_CLIENT.export import export_experiment
    from FIREQ_PLOTTER import plotting

    dirs = [Path(d) for d in request["dirs"]]
    targets = request.get("export_to") or [None] * len(dirs)
    plot_dirs = []
    for src, dst in zip(dirs, targets):
        # already exported (or nowhere to export): plot in place
        if (src / "data.pkl").is_file() or not dst:
            plot_dirs.append(str(src))
            continue
        dst = Path(dst)
        # re-export only when the export is missing or older than any source file
        newest = max((f.stat().st_mtime for f in src.glob("*")), default=0)
        if not (dst / "data.pkl").is_file() or (dst / "data.pkl").stat().st_mtime < newest:
            export_experiment(src, dst)
        plot_dirs.append(str(dst))
    func = getattr(plotting, request["function"])
    # blocks until the user closes the plot windows
    func(*plot_dirs, **request.get("kwargs", {}))
    print(f"plot closed: {request['function']} {' '.join(plot_dirs)}", flush=True)
    return 0


def main() -> int:
    """Entry point.

    :return: the process exit code.
    :rtype: int
    """
    return run(json.loads(sys.argv[1]))


if __name__ == "__main__":
    sys.exit(main())
