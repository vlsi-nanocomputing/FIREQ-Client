"""YAML generator for FIREQ benchmarking experiments."""

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader


class YAMLGenerator:
    """Generate FIREQ experiment YAML files from Jinja2 templates."""

    def __init__(self, templates_dir: str | Path | None = None) -> None:
        """Initialize the YAML generator.

        :param templates_dir: root directory containing the experiment templates.
        :type templates_dir: str | Path | None
        """
        if templates_dir is None:
            templates_dir = Path(__file__).resolve().parents[1] / "templates"

        self.environment = Environment(
            loader=FileSystemLoader(templates_dir),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, experiment: str, parameters: dict[str, Any]) -> str:
        """Render an experiment template.

        :param experiment: experiment name.
        :type experiment: str
        :param parameters: parameters passed to the template.
        :type parameters: dict[str, Any]
        :return: rendered YAML configuration.
        :rtype: str
        """
        # load the experiment template
        template = self.environment.get_template(f"{experiment}.yaml.j2")

        return template.render(**parameters)

    def generate(self, experiment: str, parameters: dict[str, Any], output_path: str | Path) -> Path:
        """Generate an experiment YAML file.

        :param experiment: experiment name.
        :type experiment: str
        :param parameters: parameters passed to the template.
        :type parameters: dict[str, Any]
        :param output_path: output YAML path.
        :type output_path: str | Path
        :return: generated YAML path.
        :rtype: Path
        """
        output_path = Path(output_path)

        # create the output directory if needed
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # render and save the experiment configuration
        output_path.write_text(self.render(experiment, parameters), encoding="utf-8")

        return output_path