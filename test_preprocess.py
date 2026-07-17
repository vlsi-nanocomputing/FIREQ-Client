import sys
import os
import yaml
from pathlib import Path
from typing import Any

from client_package.yaml_preprocessor import resolve_placeholders

def resolve_all_yamls(input_dir: str) -> None:
    input_path = Path(input_dir)
    if not input_path.is_dir():
        print(f"Error: {input_dir} is not a valid directory.")
        sys.exit(1)

    output_path = input_path / "resolved"
    output_path.mkdir(exist_ok=True)

    yaml_files = list(input_path.glob("*.yaml")) + list(input_path.glob("*.yml"))
    if not yaml_files:
        print("No .yaml or .yml files found in the directory.")
        return

    for file in yaml_files:
        try:
            with open(file, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            resolved = resolve_placeholders(raw)
            out_file = output_path / file.name
            with open(out_file, "w", encoding="utf-8") as f:
                yaml.dump(resolved, f, default_flow_style=False, sort_keys=False)
            print(f"✅ Resolved: {file.name} → {out_file}")
        except Exception as e:
            print(f"❌ Failed to process {file.name}: {e}")

if __name__ == "__main__":
    folder = input("Enter the path to the folder containing YAML files: ")
    resolve_all_yamls(folder)