import os
import ast
import networkx as nx
from collections import defaultdict
import yaml
from dataclasses import dataclass, field
import sys
import importlib.util
import logging
from typing import List, Optional, Dict, Tuple
from .exceptions import CodeAnalysisError

# Set up logger
logger = logging.getLogger(__name__)

@dataclass
class SmellParticipant:
    """
    Represents an architectural element (file, module, class, function)
    that participates in an architectural smell, beyond the primary one.
    """
    file_path: str                     # Path to the file of this participant
    element_name: str                  # Name of the module, class, or function
    element_type: str                  # Type of the element (e.g., "class", "module", "function")
    role_in_smell: str                 # Description of this participant's role in the smell
    start_line: Optional[int] = None   # Optional start line number in this participant's file
    end_line: Optional[int] = None     # Optional end line number in this participant's file

@dataclass
class ArchitecturalSmell:
    """
    Represents an architectural smell detected in the codebase.

    Attributes:
        name (str): The name of the architectural smell
        description (str): Description of the architectural smell
        file_path (str): Path to the primary file containing the architectural smell
        module_class (str): The primary module or class containing the architectural smell
        line_number (int, optional): The line number where the architectural smell was detected
        severity (str): The severity level of the architectural smell ('low', 'medium', 'high')
        related_participants (List[SmellParticipant]): List of other architectural elements involved
            in this smell beyond the primary one (e.g., all modules in a cycle, clients of a hub)
    """
    name: str
    description: str
    file_path: str
    module_class: str
    line_number: int = None
    severity: str = 'medium'
    related_participants: List[SmellParticipant] = field(default_factory=list)
    importers_by_participant: Dict[str, List[str]] = field(default_factory=dict)
    participant_dependency_edges: List[Tuple[str, str]] = field(default_factory=list)

class ArchitecturalSmellDetector:
    """
    A class to detect architectural smells in Python projects.

    This class analyzes Python source code files within a directory to identify
    various architectural smells based on predefined thresholds.

    Attributes:
        architectural_smells (list): A list to store detected architectural smells.
        module_dependencies (nx.DiGraph): A directed graph to represent module dependencies.
        module_functions (defaultdict): A dictionary to store functions for each module.
        api_usage (defaultdict): A dictionary to store API usage for each module.
        thresholds (dict): A dictionary of threshold values for various smell detections.
        file_paths (dict): A dictionary to store file paths for each module.
        external_dependencies (dict): A dictionary to store external dependencies for each module.
        function_calls (defaultdict): A dictionary to track inter-module function calls.
    """

    def __init__(self, thresholds):
        """
        Initialize the ArchitecturalSmellDetector with given thresholds.

        Args:
            thresholds (dict): A dictionary of threshold values for various smell detections.
        """
        self.architectural_smells = []
        self.module_dependencies = nx.DiGraph()
        self.module_functions = defaultdict(set)
        self.api_usage = defaultdict(list)
        self.thresholds = thresholds
        self.file_paths = {}
        self.external_dependencies = defaultdict(set)
        self.function_calls = defaultdict(set)  # Track inter-module function calls
        self.project_root = None  # Will be set during analyze_directory

    def load_thresholds(self, config_path):
        """
        Load threshold values from a YAML configuration file.

        Args:
            config_path (str): Path to the YAML configuration file.

        Returns:
            dict: A dictionary of threshold values for architectural smells.
        """
        with open(config_path, 'r') as file:
            config = yaml.safe_load(file)
        return {k: v['value'] for k, v in config['architectural_smells'].items()}

    def detect_smells(self, directory_path):
        """
        Detect architectural smells in the given directory.
        """
        detection_methods = [
            (self.detect_hub_like_dependency, "detect_hub_like_dependency"),
            (self.detect_scattered_functionality, "detect_scattered_functionality"),
            (self.detect_redundant_abstractions, "detect_redundant_abstractions"),
            (self.detect_god_objects, "detect_god_objects"),
            (self.detect_improper_api_usage, "detect_improper_api_usage"),
            (self.detect_orphan_modules, "detect_orphan_modules"),
            (self.detect_cyclic_dependencies, "detect_cyclic_dependencies"),
            (self.detect_unstable_dependencies, "detect_unstable_dependencies")
        ]

        try:
            # First analyze the directory structure
            logger.info(f"Analyzing directory structure: {directory_path}")
            self.analyze_directory(directory_path)
            
            # Then run each detection method
            for detect_method, method_name in detection_methods:
                try:
                    logger.debug(f"Running {method_name}")
                    detect_method()
                except Exception as e:
                    logger.error(f"Error in {method_name}: {str(e)}", exc_info=True)
                    raise CodeAnalysisError(
                        message=str(e),
                        file_path=directory_path,
                        function_name=method_name
                    )
                    
        except Exception as e:
            logger.error(f"Error analyzing directory {directory_path}: {str(e)}", exc_info=True)
            raise CodeAnalysisError(
                message=str(e),
                file_path=directory_path
            )

    def analyze_directory(self, directory_path):
        """
        Analyze all Python files in the given directory and its subdirectories.

        Args:
            directory_path (str): The path to the directory to be analyzed.
        """
        self.project_root = os.path.abspath(directory_path)
        for root, _, files in os.walk(directory_path):
            for file in files:
                if file.endswith('.py'):
                    file_path = os.path.join(root, file)
                    self.analyze_file(file_path)
        
        # After analyzing all files, resolve external dependencies
        self.resolve_external_dependencies()

    def analyze_file(self, file_path):
        """
        Analyze a single Python file for architectural information with improved
        intra-project dependency detection.
        """
        try:
            with open(file_path, 'r') as file:
                tree = ast.parse(file.read())

            # Get relative module path from project root
            rel_path = os.path.relpath(file_path, self.project_root)
            # splitext removes .py extension, then convert path separators to dots
            # e.g., "pkg/subpkg/module.py" -> "pkg.subpkg.module"
            module_name = os.path.splitext(rel_path)[0].replace(os.path.sep, '.')
            self.module_dependencies.add_node(module_name)
            self.file_paths[module_name] = file_path
            
            # Track local imports and their line numbers
            local_imports = []

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        import_name = alias.name
                        local_imports.append((import_name, node.lineno))
                        self.module_dependencies.add_edge(module_name, import_name)
                
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        # Handle relative imports
                        if node.level > 0:  # This is a relative import
                            current_package = module_name.split('.')
                            # Go up by node.level
                            parent_package = '.'.join(current_package[:-node.level])
                            if parent_package:
                                import_name = f"{parent_package}.{node.module}"
                            else:
                                import_name = node.module
                        else:
                            import_name = node.module
                        
                        local_imports.append((import_name, node.lineno))
                        self.module_dependencies.add_edge(module_name, import_name)
                        
                        # Track imported names for more detailed dependency analysis
                        for alias in node.names:
                            if alias.name != '*':
                                full_import = f"{import_name}.{alias.name}"
                                self.module_functions[import_name].add(alias.name)
                
                elif isinstance(node, ast.FunctionDef):
                    self.module_functions[module_name].add(node.name)
                
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute):
                        self.api_usage[module_name].append(node.func.attr)
                        
                        # Track function calls between modules
                        if isinstance(node.func.value, ast.Name):
                            # Check if this is a call to an imported module
                            module_called = node.func.value.id
                            if any(module_called == imp[0].split('.')[-1] for imp in local_imports):
                                self.function_calls[module_name].add((module_called, node.func.attr))

        except SyntaxError as e:
            print(f"Parse error in file {file_path}: {str(e)}")
        except Exception as e:
            print(f"Error analyzing file {file_path}: {str(e)}")

    def resolve_external_dependencies(self):
        """
        Resolve external dependencies while preserving intra-project dependencies.
        """
        # Get all project modules
        all_modules = set(self.module_dependencies.nodes())
        standard_lib_modules = set(sys.stdlib_module_names)

        for module in list(self.module_dependencies.nodes()):
            for dependency in list(self.module_dependencies.successors(module)):
                # Check if it's a project module by looking for the file
                possible_paths = [
                    os.path.join(self.project_root, *dependency.split('.')) + '.py',
                    os.path.join(self.project_root, dependency.split('.')[0], '__init__.py')
                ]
                
                is_project_module = (
                    dependency in all_modules or
                    any(os.path.exists(path) for path in possible_paths)
                )
                
                # Keep project dependencies, handle external ones
                if not is_project_module:
                    is_stdlib = any(dependency.startswith(std_lib) for std_lib in standard_lib_modules)
                    
                    try:
                        spec = importlib.util.find_spec(dependency.split('.')[0])
                        is_third_party = spec is not None and not is_stdlib
                    except (ModuleNotFoundError, ValueError):
                        is_third_party = False
                    
                    self.module_dependencies.remove_edge(module, dependency)
                    
                    if is_stdlib:
                        self.external_dependencies[module].add(('stdlib', dependency))
                    elif is_third_party:
                        self.external_dependencies[module].add(('third_party', dependency))
                    
                    # Remove isolated external nodes
                    if not self.module_dependencies.in_edges(dependency) and \
                       not self.module_dependencies.out_edges(dependency):
                        self.module_dependencies.remove_node(dependency)

    def _compute_importers_by_participant(self, participants: List[SmellParticipant]) -> Dict[str, List[str]]:
        """
        Compute which repo files import each participant file.

        Args:
            participants: List of SmellParticipant objects involved in the smell

        Returns:
            Dict mapping participant file paths to lists of importer file paths
        """
        importers_by_participant = {}

        # Build reverse mapping: file_path -> module_name
        file_to_module = {v: k for k, v in self.file_paths.items()}

        for participant in participants:
            participant_file = participant.file_path

            # Skip external dependencies, unknown files, or already processed
            if participant_file in ("External Library", "Unknown"):
                continue
            if participant_file in importers_by_participant:
                continue  # Already processed this file

            # Derive module from file path
            module_name = file_to_module.get(participant_file)
            if not module_name or module_name not in self.module_dependencies:
                continue

            # Find all modules that import this participant
            importers = []
            for predecessor in self.module_dependencies.predecessors(module_name):
                predecessor_file = self.file_paths.get(predecessor, "Unknown")
                if predecessor_file != "Unknown":
                    importers.append(predecessor_file)

            importers_by_participant[participant_file] = sorted(set(importers))

        return importers_by_participant

    def _compute_participant_dependency_edges(self, participants: List[SmellParticipant]) -> List[Tuple[str, str]]:
        """
        Compute directed dependency edges among participant files.

        Args:
            participants: List of SmellParticipant objects involved in the smell

        Returns:
            List of (src_file, dst_file) tuples representing edges where src imports dst
        """
        # Build reverse mapping: file_path -> module_name
        file_to_module = {v: k for k, v in self.file_paths.items()}

        # Collect unique participant files and their modules
        participant_files = set()
        file_to_module_filtered = {}

        for participant in participants:
            participant_file = participant.file_path
            if participant_file in ("External Library", "Unknown"):
                continue

            module_name = file_to_module.get(participant_file)
            if module_name and module_name in self.module_dependencies:
                participant_files.add(participant_file)
                file_to_module_filtered[participant_file] = module_name

        # Find edges where both source and target are participant files
        edges = []
        for src_file in participant_files:
            src_module = file_to_module_filtered[src_file]
            for successor in self.module_dependencies.successors(src_module):
                dst_file = self.file_paths.get(successor)
                if dst_file in participant_files:
                    edges.append((src_file, dst_file))

        return sorted(edges)

    def add_smell(self, name, description, file_path, module_class, line_number=None,
                  severity='medium', related_participants=None):
        """
        Add a detected architectural smell to the list.
        
        Args:
            name (str): The name of the smell
            description (str): Description of the smell
            file_path (str): Path to the file containing the smell
            module_class (str): The module or class containing the smell
            line_number (int, optional): The line number where the smell was detected
            severity (str, optional): The severity level of the smell (default: 'medium')
            related_participants (List[SmellParticipant], optional): List of related elements involved in the smell
        """
        if related_participants is None:
            related_participants = []

        # Compute importers and dependency edges for participants
        importers_by_participant = self._compute_importers_by_participant(related_participants)
        participant_dependency_edges = self._compute_participant_dependency_edges(related_participants)

        self.architectural_smells.append(ArchitecturalSmell(
            name=name,
            description=description,
            file_path=file_path,
            module_class=module_class,
            line_number=line_number,
            severity=severity,
            related_participants=related_participants,
            importers_by_participant=importers_by_participant,
            participant_dependency_edges=participant_dependency_edges
        ))

    def detect_hub_like_dependency(self):
        """
        Detect hub-like dependencies in the project with improved accuracy.
        """
        total_modules = len(self.module_dependencies.nodes())
        if total_modules < 3:  # Skip analysis for very small projects
            return
            
        threshold = self.thresholds.get('HUB_LIKE_DEPENDENCY_THRESHOLD', 0.5)
        min_connections = self.thresholds.get('MIN_HUB_CONNECTIONS', 5)
        
        for node in self.module_dependencies.nodes():
            # Count both internal and external dependencies
            in_degree = self.module_dependencies.in_degree(node)
            out_degree = self.module_dependencies.out_degree(node)
            external_deps = len(self.external_dependencies[node])
            total_connections = in_degree + out_degree + external_deps
            
            # Calculate fan-in and fan-out ratios
            fan_in_ratio = in_degree / total_modules if total_modules > 0 else 0
            fan_out_ratio = (out_degree + external_deps) / total_modules if total_modules > 0 else 0
            
            # Check for hub-like characteristics
            is_hub = (total_connections >= min_connections and 
                     (total_connections / total_modules) > threshold)
            
            # Additional checks to reduce false positives
            if is_hub:
                # Exclude common infrastructure modules
                if any(pattern in node.lower() for pattern in ['util', 'common', 'base', 'core']):
                    continue
                    
                # Check if the module has balanced dependencies
                is_balanced = 0.2 <= fan_in_ratio / (fan_out_ratio + 0.0001) <= 5
                
                if not is_balanced:
                    related_participants = []

                    # Collect incoming dependencies (modules that depend on this hub)
                    for predecessor in self.module_dependencies.predecessors(node):
                        predecessor_file = self.file_paths.get(predecessor, "Unknown")
                        related_participants.append(SmellParticipant(
                            file_path=predecessor_file,
                            element_name=predecessor,
                            element_type="module",
                            role_in_smell="Dependent on Hub",
                        ))

                    # Collect outgoing dependencies (modules that the hub depends on)
                    for successor in self.module_dependencies.successors(node):
                        successor_file = self.file_paths.get(successor, "Unknown")
                        related_participants.append(SmellParticipant(
                            file_path=successor_file,
                            element_name=successor,
                            element_type="module",
                            role_in_smell="Hub Dependency",
                        ))

                    # Add external dependencies if any
                    for dep_type, dep_name in self.external_dependencies[node]:
                        related_participants.append(SmellParticipant(
                            file_path="External Library",
                            element_name=dep_name,
                            element_type=dep_type,
                            role_in_smell="External Dependency of Hub",
                        ))

                    self.add_smell(
                        "Hub-like Dependency",
                        f"Module '{node}' is a potential hub with {total_connections} connections "
                        f"(in: {in_degree}, out: {out_degree}, external: {external_deps})",
                        self.file_paths.get(node, "Unknown"),
                        node,
                        severity='high' if total_connections > min_connections * 2 else 'medium',
                        related_participants=related_participants
                    )

    def detect_scattered_functionality(self):
        """
        Detect scattered functionality in the project.
        """
        function_modules = defaultdict(list)
        min_function_length = 3  # Ignore very short function names
        excluded_names = {'main', 'init', 'setup', 'test'}  # Common function names to exclude
        
        for module, functions in self.module_functions.items():
            for func in functions:
                # Skip common/utility functions and short names
                if (len(func) >= min_function_length and 
                    func.lower() not in excluded_names and 
                    not func.startswith('_')):  # Skip private functions
                    function_modules[func].append(module)
        
        min_occurrences = self.thresholds.get('MIN_SCATTERED_OCCURRENCES', 3)
        for func, modules in function_modules.items():
            if len(modules) >= min_occurrences:  # Increase minimum occurrences threshold
                # Create related participants for all modules involved
                related_participants = []
                for module in modules:
                    # Include all modules, including the "primary" one, for completeness
                    related_participants.append(SmellParticipant(
                        file_path=self.file_paths.get(module, "Unknown"),
                        element_name=module,
                        element_type="module",
                        role_in_smell=f"Contains implementation of '{func}' function"
                    ))

                self.add_smell(
                    "Scattered Functionality",
                    f"Function '{func}' appears in {len(modules)} modules: {', '.join(modules)}",
                    self.file_paths.get(modules[0], "Unknown"),
                    modules[0],
                    related_participants=related_participants
                )

    def detect_redundant_abstractions(self):
        """
        Detect potential redundant abstractions in the project.
        """
        similar_modules = defaultdict(list)
        min_functions = 3  # Minimum number of functions to consider
        
        for module, functions in self.module_functions.items():
            # Only consider modules with sufficient functions
            if len(functions) >= min_functions:
                # Filter out private functions and common utility functions
                public_functions = {f for f in functions 
                                 if not f.startswith('_') 
                                 and len(f) > 3 
                                 and f.lower() not in {'main', 'init', 'setup', 'test'}}
                
                if public_functions:  # Only proceed if there are public functions
                    signature = frozenset(public_functions)
                    similar_modules[signature].append(module)
        
        similarity_threshold = self.thresholds.get('REDUNDANT_SIMILARITY_THRESHOLD', 0.8)
        for signature, modules in similar_modules.items():
            if len(modules) > 1 and len(signature) >= min_functions:
                # Calculate similarity score between modules
                for i in range(len(modules)):
                    for j in range(i + 1, len(modules)):
                        module1 = modules[i]
                        module2 = modules[j]
                        module1_funcs = self.module_functions[module1]
                        module2_funcs = self.module_functions[module2]
                        common_funcs = module1_funcs & module2_funcs
                        all_funcs = module1_funcs | module2_funcs
                        similarity = len(common_funcs) / len(all_funcs)
                        
                        if similarity >= similarity_threshold:
                            # Create related participants
                            related_participants = []

                            # First add the redundant module
                            related_participants.append(SmellParticipant(
                                file_path=self.file_paths.get(module2, "Unknown"),
                                element_name=module2,
                                element_type="module",
                                role_in_smell=f"Redundant with {module1} (similarity: {similarity:.1%})"
                            ))

                            # Add details about the common functions
                            common_funcs_sorted = sorted(common_funcs)
                            for func in common_funcs_sorted:
                                related_participants.append(SmellParticipant(
                                    file_path=self.file_paths.get(module1, "Unknown"),
                                    element_name=f"{module1}.{func}",
                                    element_type="function",
                                    role_in_smell=f"Duplicated in both modules"
                                ))

                            self.add_smell(
                                "Potential Redundant Abstractions",
                                f"Modules {module1} and {module2} have {similarity:.1%} similar functionalities",
                                self.file_paths.get(module1, "Unknown"),
                                module1,
                                related_participants=related_participants
                            )

    def detect_god_objects(self):
        """
        Detect god objects in the project.
        """
        min_functions = self.thresholds.get('MIN_GOD_OBJECT_FUNCTIONS', 5)
        excluded_patterns = {'test_', 'setup_', 'config_'}  # Common prefixes to exclude
        
        for module, functions in self.module_functions.items():
            # Filter out private methods and common test/setup functions
            public_functions = {f for f in functions 
                              if not f.startswith('_') and 
                              not any(f.startswith(pattern) for pattern in excluded_patterns)}
            
            if (len(public_functions) >= min_functions and 
                len(public_functions) > self.thresholds['GOD_OBJECT_FUNCTIONS']):
                related_participants = []

                # For God Objects, find related modules it interacts with
                # Track dependencies (modules it imports/uses)
                for successor in self.module_dependencies.successors(module):
                    successor_file = self.file_paths.get(successor, "Unknown")
                    related_participants.append(SmellParticipant(
                        file_path=successor_file,
                        element_name=successor,
                        element_type="module",
                        role_in_smell="Used by God Object"
                    ))

                # Also include function calls to other modules
                for module_called, func_called in self.function_calls.get(module, []):
                    # Try to find the target module
                    for possible_module in self.module_functions:
                        if possible_module.endswith(module_called) and func_called in self.module_functions[possible_module]:
                            module_file = self.file_paths.get(possible_module, "Unknown")
                            related_participants.append(SmellParticipant(
                                file_path=module_file,
                                element_name=f"{possible_module}.{func_called}",
                                element_type="function",
                                role_in_smell="Function called by God Object"
                            ))

                self.add_smell(
                    "God Object",
                    f"Module '{module}' has too many public functions ({len(public_functions)})", 
                    self.file_paths.get(module, "Unknown"),
                    module,
                    related_participants=related_participants
                )

    def detect_improper_api_usage(self):
        """
        Detect potential improper API usage in the project.
        """
        min_calls = self.thresholds.get('MIN_API_CALLS', 10)  # Minimum calls to consider
        repetition_threshold = self.thresholds.get('API_REPETITION_THRESHOLD', 0.4)
        
        for module, api_calls in self.api_usage.items():
            if len(api_calls) >= min_calls:
                # Count frequency of each API call
                call_frequency = {}
                for call in api_calls:
                    call_frequency[call] = call_frequency.get(call, 0) + 1
                
                # Check for highly repetitive calls
                repetitive_calls = {call: count for call, count in call_frequency.items() 
                                  if count >= 3}  # Ignore calls repeated less than 3 times
                
                if (repetitive_calls and 
                    sum(repetitive_calls.values()) / len(api_calls) > repetition_threshold):
                    # Create related participants for API usage patterns
                    related_participants = []

                    # Add repetitive API calls as participants
                    for call, count in sorted(repetitive_calls.items(), key=lambda x: x[1], reverse=True):
                        related_participants.append(SmellParticipant(
                            file_path=self.file_paths.get(module, "Unknown"),
                            element_name=call,
                            element_type="api_call",
                            role_in_smell=f"Repetitive API call ({count} occurrences)"
                        ))

                    # Look for similar modules with better API usage patterns
                    for other_module, other_calls in self.api_usage.items():
                        if other_module != module and len(other_calls) >= min_calls:
                            # Check if this module uses similar APIs but in a less repetitive way
                            other_call_freq = {}
                            for call in other_calls:
                                other_call_freq[call] = other_call_freq.get(call, 0) + 1

                            # Check if the other module uses some of the same APIs
                            common_apis = set(repetitive_calls.keys()) & set(other_call_freq.keys())
                            if common_apis:
                                # Check if the other module uses these APIs less repetitively
                                other_repetition = sum(other_call_freq[call] for call in common_apis) / len(other_calls)
                                if other_repetition < (sum(repetitive_calls.values()) / len(api_calls)):
                                    related_participants.append(SmellParticipant(
                                        file_path=self.file_paths.get(other_module, "Unknown"),
                                        element_name=other_module,
                                        element_type="module",
                                        role_in_smell="Reference module with better API usage pattern"
                                    ))
                                    break  # Just add one example of better usage

                    self.add_smell(
                        "Potential Improper API Usage",
                        f"Module '{module}' has repetitive API calls: " +
                        ", ".join(f"{call}({count}x)" for call, count in repetitive_calls.items()),
                        self.file_paths.get(module, "Unknown"),
                        module,
                        related_participants=related_participants
                    )

    def detect_orphan_modules(self):
        """
        Detect orphan modules in the project.
        """
        excluded_modules = {'__init__', 'setup', 'tests', 'utils'}  # Common standalone modules
        min_project_size = self.thresholds.get('MIN_PROJECT_SIZE', 3)
        
        if len(self.module_dependencies.nodes()) < min_project_size:
            return
            
        for node in self.module_dependencies.nodes():
            module_name = node.split('.')[-1]
            # Fix: Check if any excluded module name is in the full node path
            if (self.module_dependencies.in_degree(node) + self.module_dependencies.out_degree(node) == 0 and
                module_name not in excluded_modules and
                not any(excluded in node.lower() for excluded in excluded_modules)):

                # For orphan modules, find potential integration candidates based on functionality
                related_participants = []
                orphan_functions = self.module_functions.get(node, set())

                # Look for modules with similar functionality that could be integrated with this orphan
                for other_node in self.module_dependencies.nodes():
                    if other_node != node and not any(excluded in other_node.lower() for excluded in excluded_modules):
                        other_functions = self.module_functions.get(other_node, set())

                        # Check for function name overlap as a sign of potential integration opportunity
                        common_functions = orphan_functions.intersection(other_functions)
                        if common_functions:
                            related_participants.append(SmellParticipant(
                                file_path=self.file_paths.get(other_node, "Unknown"),
                                element_name=other_node,
                                element_type="module",
                                role_in_smell=f"Potential integration candidate (shares {len(common_functions)} similar functions)"
                            ))

                # If no function overlap is found, suggest modules in similar directories
                if not related_participants and self.file_paths.get(node, ""):
                    orphan_dir = os.path.dirname(self.file_paths.get(node, ""))

                    for other_node in self.module_dependencies.nodes():
                        if other_node != node and self.file_paths.get(other_node, ""):
                            other_dir = os.path.dirname(self.file_paths.get(other_node, ""))

                            # If modules are in the same directory, they might be related
                            if other_dir == orphan_dir:
                                related_participants.append(SmellParticipant(
                                    file_path=self.file_paths.get(other_node, "Unknown"),
                                    element_name=other_node,
                                    element_type="module",
                                    role_in_smell="Potential integration candidate (same directory)"
                                ))

                self.add_smell(
                    name="Orphan Module",
                    description=f"'{node}' is isolated from other modules",
                    file_path=self.file_paths.get(node, "Unknown"),
                    module_class=node,
                    severity='medium',
                    related_participants=related_participants
                )

    def detect_cyclic_dependencies(self):
        """
        Detect cyclic dependencies with improved accuracy and cycle classification.
        """
        min_cycle_size = self.thresholds.get('MIN_CYCLE_SIZE', 2)
        max_cycle_size = self.thresholds.get('MAX_CYCLE_SIZE', 5)
        excluded_modules = {'__init__', 'utils', 'common', 'base', 'core'}
        
        # Find all simple cycles
        cycles = list(nx.simple_cycles(self.module_dependencies))
        
        # Group cycles by their shared nodes to identify related cycles
        cycle_groups = defaultdict(list)
        
        for cycle in cycles:
            if min_cycle_size <= len(cycle) <= max_cycle_size:
                # Skip cycles containing excluded modules
                if any(any(excluded in node.lower() for excluded in excluded_modules) 
                      for node in cycle):
                    continue
                
                # Calculate cycle metrics
                cycle_strength = 0
                for i in range(len(cycle)):
                    node1 = cycle[i]
                    node2 = cycle[(i + 1) % len(cycle)]
                    # Count mutual dependencies
                    cycle_strength += sum(1 for _ in nx.all_simple_paths(
                        self.module_dependencies, node1, node2))
                
                # Group related cycles
                cycle_key = frozenset(cycle)
                cycle_groups[cycle_key].append((cycle, cycle_strength))
        
        # Report cycles with additional context
        for cycle_group in cycle_groups.values():
            strongest_cycle = max(cycle_group, key=lambda x: x[1])
            cycle, strength = strongest_cycle
            
            # Calculate severity based on cycle size and strength
            severity = 'high' if len(cycle) >= 3 and strength >= 3 else 'medium'
            
            cycle_str = ' -> '.join(cycle + [cycle[0]])

            # Create related participants for all modules in the cycle
            related_participants = []
            for node in cycle:
                # Add all nodes in the cycle as participants, including the "primary" one
                # for a more complete representation of the cycle
                node_file = self.file_paths.get(node, "Unknown")
                related_participants.append(SmellParticipant(
                    file_path=node_file,
                    element_name=node,
                    element_type="module",
                    role_in_smell=f"Member of cycle"
                ))

            self.add_smell(
                "Cyclic Dependency",
                f"Strong cyclic dependency detected: {cycle_str}; Cycle strength: {strength} mutual dependencies",
                self.file_paths.get(cycle[0], "Unknown"),
                cycle[0],
                severity=severity,
                related_participants=related_participants
            )

    def detect_unstable_dependencies(self):
        """
        Detect unstable dependencies in the project.
        """
        min_dependencies = self.thresholds.get('MIN_DEPENDENCIES', 5)  # Minimum dependencies to consider
        excluded_patterns = {'test_', 'setup_', '__init__'}  # Patterns to exclude
        
        for node in self.module_dependencies.nodes():
            if any(pattern in node for pattern in excluded_patterns):
                continue
                
            in_degree = self.module_dependencies.in_degree(node)
            out_degree = self.module_dependencies.out_degree(node)
            total_dependencies = in_degree + out_degree
            
            if total_dependencies >= min_dependencies:
                instability = out_degree / total_dependencies
                if instability > self.thresholds['UNSTABLE_DEPENDENCY_THRESHOLD']:
                    related_participants = []

                    # Track outgoing dependencies (what this module depends on)
                    for successor in self.module_dependencies.successors(node):
                        successor_file = self.file_paths.get(successor, "Unknown")
                        successor_in_degree = self.module_dependencies.in_degree(successor)
                        successor_out_degree = self.module_dependencies.out_degree(successor)
                        successor_total = successor_in_degree + successor_out_degree
                        successor_instability = successor_out_degree / successor_total if successor_total > 0 else 0

                        # Calculate stability comparison
                        stability_comparison = ""
                        if successor_total >= min_dependencies:
                            if successor_instability > instability:
                                stability_comparison = " (more unstable)"
                            elif successor_instability < instability:
                                stability_comparison = " (more stable)"

                        related_participants.append(SmellParticipant(
                            file_path=successor_file,
                            element_name=successor,
                            element_type="module",
                            role_in_smell=f"Dependency with instability {successor_instability:.2f}{stability_comparison}"
                        ))

                    # Also add external dependencies if any
                    for dep_type, dep_name in self.external_dependencies[node]:
                        related_participants.append(SmellParticipant(
                            file_path="External Library",
                            element_name=dep_name,
                            element_type=dep_type,
                            role_in_smell="External Dependency"
                        ))

                    self.add_smell(
                        "Unstable Dependency",
                        f"Module '{node}' has high instability ({instability:.2f}) " +
                        f"with {out_degree} outgoing and {in_degree} incoming dependencies",
                        self.file_paths.get(node, "Unknown"),
                        node,
                        related_participants=related_participants
                    )

    def print_report(self):
        """
        Print a report of all detected architectural smells.

        If no smells are detected, it prints a message indicating so.
        """
        if not self.architectural_smells:
            print("No architectural smells detected.")
        else:
            print("Detected Architectural Smells:")
            for smell in self.architectural_smells:
                print(f"- {smell.name} in {smell.module_class}")
                print(f"  File: {smell.file_path}")
                print(f"  Description: {smell.description}")
                print(f"  Severity: {smell.severity}")
                if smell.line_number:
                    print(f"  Line: {smell.line_number}")

                if smell.related_participants:
                    print("  Related participants:")
                    for i, participant in enumerate(smell.related_participants, 1):
                        print(f"    {i}. {participant.element_name} ({participant.element_type})")
                        print(f"       Role: {participant.role_in_smell}")
                        print(f"       File: {participant.file_path}")
                        if participant.start_line:
                            line_info = f"Line: {participant.start_line}"
                            if participant.end_line:
                                line_info += f" to {participant.end_line}"
                            print(f"       {line_info}")
                print()

def analyze_architecture(directory_path, config_path):
    """
    Analyze the architecture of a Python project and detect architectural smells.

    Args:
        directory_path (str): The path to the directory containing the Python project to analyze.
        config_path (str): The path to the configuration file containing smell detection thresholds.
    """
    detector = ArchitecturalSmellDetector(config_path)
    detector.detect_smells(directory_path)
    detector.print_report()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Detect architectural smells in Python code.")
    parser.add_argument("directory", help="Directory path to analyze")
    parser.add_argument("--config", default="code_quality_config.yaml", help="Path to the configuration file")
    args = parser.parse_args()

    analyze_architecture(args.directory, args.config)