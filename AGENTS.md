# Repository Guidelines

## Project Structure & Module Organization
Source code lives in `src/`, exposing the `code_quality_analyzer` package and the `analyze_code_quality` console script defined in `setup.py`. Tests live in `tests/` and rely on fixtures in `tests/conftest.py`. Documentation sources sit under `docs/` (Sphinx configuration in `docs/source/conf.py`), and `code_quality_config.yaml` in the repo root provides default thresholds used by both the CLI and the test suite.

## Build, Test, and Development Commands
Create a virtual environment (`python -m venv venv` then `source venv/bin/activate` or `venv\Scripts\activate` on Windows) and install dependencies with `pip install -e ".[dev]"`. Run the CLI locally via `analyze_code_quality path/to/project --config code_quality_config.yaml` to verify behavior. Execute the automated tests with `pytest tests/`. Build the documentation after installing `pip install -r docs/requirements.txt` and running `make html` from `docs/` or `sphinx-build -b html docs/source docs/_build`.

## Coding Style & Naming Conventions
Follow standard PEP 8 guidelines (4-space indentation, descriptive snake_case for functions and variables, PascalCase for classes). Keep modules small and cohesive; break out helpers into `src/code_quality_analyzer/...` submodules when they gain multiple responsibilities. There is no enforced formatter, so format and lint manually before submitting. Keep configuration constants centralized in YAML or dedicated config modules rather than scattering literals through detectors.

## Testing Guidelines
The project uses `pytest` with tests placed under `tests/` and named `test_*.py`. Prefer fixture-based setups (see `tests/conftest.py`) and cover both positive detections and false-positive avoidance. Before opening a pull request, run `pytest tests/` locally; there are no coverage thresholds, but add assertions that validate new smells or configuration pathways to keep regressions visible.

## Commit & Pull Request Guidelines
Use concise, imperative commit messages (e.g., `Add structural smell detector fallback`). Reference any related GitHub issues in the description when available. Every pull request should summarize the problem, describe the solution, list testing evidence (command output snippets), and include screenshots or sample reports if UI/output changes occur. For significant feature work, open an issue or discussion first so reviewers can align on scope.

## Documentation & Configuration Tips
When changing CLI options or configuration keys, update `README.md`, `docs/`, and `code_quality_config.yaml` simultaneously to keep users aligned. Regenerate docs (`make html`) before pushing to ensure Sphinx builds cleanly. Treat `code_quality_config.yaml` as the canonical reference for thresholds—tests import it directly, so changes here may require fixture updates.
