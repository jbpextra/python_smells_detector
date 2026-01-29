import os
import yaml
import logging
import warnings
import importlib.resources as pkg_resources

logger = logging.getLogger(__name__)

class ConfigHandler:
    """
    A class to handle configuration loading and management for code quality analysis.
    """

    def __init__(self, config_path):
        """
        Initialize the ConfigHandler with a given configuration file path.

        Args:
            config_path (str): The path to the YAML configuration file.
        """
        self.config_path = config_path
        self.thresholds = self.load_thresholds()
        self._validate_thresholds()

    def load_thresholds(self):
        """
        Load threshold values from the YAML configuration file.
        """
        try:
            with open(self.config_path, 'r', encoding='utf-8') as file:
                config = yaml.safe_load(file)
                
            logger.info(f"Loading configuration from: {self.config_path}")
            
            thresholds = {
                'code_smells': {k: v['value'] for k, v in config.get('code_smells', {}).items()},
                'architectural_smells': {k: v['value'] for k, v in config.get('architectural_smells', {}).items()},
                'structural_smells': {k: v['value'] for k, v in config.get('structural_smells', {}).items()}
            }
            
            logger.info(f"Loaded thresholds: {thresholds}")
            return thresholds
            
        except FileNotFoundError:
            logger.warning(
                "Configuration file not found at '%s'. Attempting to load packaged default.",
                self.config_path,
            )
            thresholds = self._load_packaged_config()
            if thresholds is not None:
                return thresholds
            logger.error(f"Configuration file not found: {self.config_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML configuration: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error loading configuration: {str(e)}")
            raise

    def _load_packaged_config(self):
        """Attempt to load the default configuration shipped with the package."""
        default_name = os.path.basename(self.config_path)
        if default_name != 'code_quality_config.yaml':
            return None

        try:
            config, resource_path = self._read_packaged_config(default_name)

            thresholds = {
                'code_smells': {k: v['value'] for k, v in config.get('code_smells', {}).items()},
                'architectural_smells': {k: v['value'] for k, v in config.get('architectural_smells', {}).items()},
                'structural_smells': {k: v['value'] for k, v in config.get('structural_smells', {}).items()}
            }
            self.config_path = resource_path
            return thresholds
        except FileNotFoundError:
            logger.error("Packaged default configuration '%s' is missing.", default_name)
        except Exception as exc:
            logger.error("Failed to load packaged configuration '%s': %s", default_name, exc)
        return None

    def _read_packaged_config(self, resource_name):
        """Load a packaged resource, falling back for older Python versions."""
        files_accessor = getattr(pkg_resources, 'files', None)

        if callable(files_accessor):
            resource = files_accessor('code_quality_analyzer').joinpath(resource_name)
            with resource.open('r', encoding='utf-8') as file:
                return yaml.safe_load(file), str(resource)

        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            with pkg_resources.path('code_quality_analyzer', resource_name) as resource:
                with open(resource, 'r', encoding='utf-8') as file:
                    return yaml.safe_load(file), str(resource)

    def _validate_thresholds(self):
        """
        Validate that all required thresholds are present and have valid values.
        """
        required_structural_thresholds = [
            'NOM_THRESHOLD', 'WMPC1_THRESHOLD', 'WMPC2_THRESHOLD', 
            'SIZE2_THRESHOLD', 'WAC_THRESHOLD', 'LCOM_THRESHOLD',
            'RFC_THRESHOLD', 'NOCC_THRESHOLD', 'DIT_THRESHOLD',
            'LOC_THRESHOLD', 'CBO_THRESHOLD'
        ]
        
        structural_thresholds = self.thresholds.get('structural_smells', {})
        
        missing_thresholds = [
            threshold for threshold in required_structural_thresholds 
            if threshold not in structural_thresholds
        ]
        
        if missing_thresholds:
            logger.warning(f"Missing required structural thresholds: {missing_thresholds}")

        for threshold, value in structural_thresholds.items():
            if not isinstance(value, (int, float)) or value <= 0:
                logger.warning(f"Invalid threshold value for {threshold}: {value}")

    def get_thresholds(self, smell_type):
        """
        Get threshold values for a specific type of code smell.
        """
        thresholds = self.thresholds.get(smell_type, {})
        logger.debug(f"Retrieved {smell_type} thresholds: {thresholds}")
        return thresholds
