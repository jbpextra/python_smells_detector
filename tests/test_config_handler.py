import pytest

from code_quality_analyzer.config_handler import ConfigHandler


def test_loads_packaged_default_when_missing(monkeypatch, tmp_path):
    """ConfigHandler should fall back to the packaged config when cwd lacks the file."""
    monkeypatch.chdir(tmp_path)

    handler = ConfigHandler('code_quality_config.yaml')

    thresholds = handler.get_thresholds('code_smells')
    assert thresholds  # ensures defaults were loaded


def test_missing_custom_config_raises(monkeypatch, tmp_path):
    """Non-default config paths should still raise if the file is missing."""
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FileNotFoundError):
        ConfigHandler('custom_thresholds.yaml')
