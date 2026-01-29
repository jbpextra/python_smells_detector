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


def test_packaged_default_loads_without_files_api(monkeypatch, tmp_path):
    """Fallback should work even if importlib.resources lacks the files helper."""
    monkeypatch.chdir(tmp_path)

    import code_quality_analyzer.config_handler as config_handler

    monkeypatch.delattr(config_handler.pkg_resources, 'files', raising=False)

    handler = config_handler.ConfigHandler('code_quality_config.yaml')

    thresholds = handler.get_thresholds('code_smells')
    assert thresholds
    assert handler.config_path.endswith('code_quality_config.yaml')
