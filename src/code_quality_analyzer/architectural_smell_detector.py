import ast
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import networkx as nx
from tqdm import tqdm

from .exceptions import CodeAnalysisError
from .project import (
    ModuleIndex,
    external_kind,
    iter_imports,
    iter_python_files,
    module_parts,
    project_root,
    root_package,
)
from .project import module_name as project_module_name

# Set up logger
logger = logging.getLogger(__name__)


@dataclass
class SmellParticipant:
    """An architectural element involved in a finding, with its role and location."""

    file_path: str
    element_name: str
    element_type: str
    role_in_smell: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None


@dataclass
class ArchitecturalSmell:
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
        errors (list): Files that could not be read or parsed.
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
        self.function_calls = defaultdict(set)  # canonical (module, function) call targets
        self._call_targets = {}  # module -> ordered candidate groups for imported calls
        self.thresholds = thresholds
        self.file_paths = {}  # New attribute to store file paths
        self.external_dependencies = defaultdict(set)
        self.project_root = None  # Set once in analyze_directory for consistent module naming
        self.root_package = None  # name of project_root when it is itself a package
        self._imports = {}  # module -> candidate-name lists, resolved once all files are known
        self.errors = []  # files that could not be read or parsed

    def detect_smells(self, directory_path, ignore_dirs=None):
        """
        Detect architectural smells in the given directory.

        Args:
            directory_path (str): The path to the directory to analyze.
            ignore_dirs (iterable): Directory names to skip during analysis.
        """
        detection_methods = [
            (self.detect_hub_like_dependency, "Hub-like Dependency"),
            (self.detect_scattered_functionality, "Scattered Functionality"),
            (self.detect_redundant_abstractions, "Redundant Abstractions"),
            (self.detect_god_objects, "God Objects"),
            (self.detect_improper_api_usage, "Improper API Usage"),
            (self.detect_orphan_modules, "Orphan Modules"),
            (self.detect_cyclic_dependencies, "Cyclic Dependencies"),
            (self.detect_unstable_dependencies, "Unstable Dependencies"),
        ]

        try:
            logger.info(f"Analyzing directory structure: {directory_path}")
            self.analyze_directory(directory_path, ignore_dirs=ignore_dirs)

            for detect_method, method_name in tqdm(detection_methods, desc="Architectural checks",
                                                   disable=None):
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

    def analyze_directory(self, directory_path, ignore_dirs=None):
        """
        Analyze all Python files in the given directory (or the single given file).

        Args:
            directory_path (str): The path to the directory to be analyzed.
            ignore_dirs (iterable): Directory names to skip.
        """
        self.project_root = project_root(directory_path)
        self.root_package = root_package(self.project_root)
        for file_path in iter_python_files(directory_path, ignore_dirs):
            self.analyze_file(file_path)

        # After analyzing all files, resolve imports to project modules or external ones
        self.resolve_external_dependencies()

    def analyze_file(self, file_path):
        """
        Analyze a single Python file for architectural information. Files that cannot
        be read or parsed are recorded in ``errors`` and skipped.
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                tree = ast.parse(file.read())
        except (SyntaxError, ValueError, UnicodeDecodeError, OSError, RecursionError) as e:
            logger.warning(f"Skipping {file_path}: {e}")
            self.errors.append({'file': file_path, 'detector': 'ArchitecturalSmellDetector',
                                'error': f"{type(e).__name__}: {e}"})
            return

        module_name = project_module_name(file_path, self.project_root)
        self.module_dependencies.add_node(module_name)
        self.file_paths[module_name] = file_path

        # Module functions are its module-level defs, not methods, nested or imported names
        self.module_functions[module_name].update(
            n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))

        imports = list(iter_imports(tree, module_name))
        self._imports[module_name] = [candidates for candidates, _ in imports]
        # Collect bound import names without reviving imports under TYPE_CHECKING.
        import_lines = {lineno for _, lineno in imports}
        bindings = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if node.lineno not in import_lines:
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split('.')[0]
                    bindings[bound] = [alias.name if alias.asname else bound]
            elif isinstance(node, ast.ImportFrom):
                imported = iter_imports(ast.Module(body=[node], type_ignores=[]), module_name)
                for alias, (candidates, _) in zip(node.names, imported):
                    bindings[alias.asname or alias.name] = candidates

        call_targets = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                self.api_usage[module_name].append(node.func.attr)
            parts = []
            target = node.func
            while isinstance(target, ast.Attribute):
                parts.append(target.attr)
                target = target.value
            if isinstance(target, ast.Name) and target.id in bindings:
                suffix = '.'.join(reversed(parts))
                call_targets.add(tuple(
                    f"{imported}.{suffix}" if suffix else imported
                    for imported in bindings[target.id]))
        self._call_targets[module_name] = sorted(call_targets)

    def resolve_external_dependencies(self):
        """
        Turn the recorded imports into edges between project modules; everything
        that is not a project module is kept in ``external_dependencies`` instead.
        """
        index = ModuleIndex(self.file_paths)
        for module, imports in self._imports.items():
            for candidates in imports:
                target = index.resolve(candidates)
                if target is None:
                    name = candidates[-1]
                    self.external_dependencies[module].add((external_kind(name), name))
                elif target != module:
                    self.module_dependencies.add_edge(module, target)

        for module, calls in self._call_targets.items():
            for candidates in calls:
                for qualified_name in candidates:
                    target_name, separator, function = qualified_name.rpartition('.')
                    target = index.resolve([target_name]) if separator else None
                    if target is not None:
                        if function in self.module_functions[target]:
                            self.function_calls[module].add((target, function))
                        # A resolved imported module wins over its package fallback,
                        # even when this call does not name a module-level function.
                        break

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

    def add_smell(self, name, description, file_path, module_class,
                  line_number=None, severity='medium', related_participants=None):
        """
        Add a detected architectural smell to the list.

        Args:
            name (str): The name of the smell
            description (str): Description of the smell
            file_path (str): Path to the file containing the smell
            module_class (str): The module or class containing the smell
            line_number (int, optional): The line number where the smell was detected
            severity (str, optional): The severity level of the smell (default: 'medium')
            related_participants (list, optional): Other elements involved in this finding.
        """
        participants = list(related_participants or [])
        importers = self._compute_importers_by_participant(participants)
        edges = self._compute_participant_dependency_edges(participants)
        self.architectural_smells.append(ArchitecturalSmell(
            name=name,
            description=description,
            file_path=file_path,
            module_class=module_class,
            line_number=line_number,
            severity=severity,
            related_participants=participants,
            importers_by_participant=importers,
            participant_dependency_edges=edges
        ))

    def _core_graph(self):
        """
        The module graph without package __init__ modules: a package importing its
        own submodules to build its API is neither a real dependency nor a cycle.
        """
        return self.module_dependencies.subgraph(
            n for n in self.module_dependencies if not n.endswith('__init__'))

    def _has_part(self, module, names):
        """
        True if one of the dotted parts of module is in names (pkg.utils, not database).
        The name of the analyzed package itself is not a part: analyzing a package
        called utils must not exclude all of it.
        """
        return any(part in names for part in module_parts(module, self.root_package))

    def detect_hub_like_dependency(self):
        """
        Detect hub-like dependencies: modules that many modules depend on and that
        themselves depend on many modules, so changes ripple through them both ways.
        """
        graph = self._core_graph()
        total_modules = len(graph)
        if total_modules < 3:  # Skip analysis for very small projects
            return

        threshold = self.thresholds.get('HUB_LIKE_DEPENDENCY_RATIO', 0.5)
        min_connections = self.thresholds.get('MIN_HUB_CONNECTIONS', 5)

        for node in graph.nodes():
            # Shared infrastructure modules are meant to be widely connected
            if self._has_part(node, {'util', 'utils', 'common', 'base', 'core'}):
                continue

            in_degree = graph.in_degree(node)
            out_degree = graph.out_degree(node)
            total_connections = in_degree + out_degree

            # A hub has both incoming and outgoing dependencies in proportion; a module
            # that is only imported (a library) or only imports (an entry point) is not one
            is_balanced = in_degree and out_degree and 0.2 <= in_degree / out_degree <= 5
            if (is_balanced and total_connections >= min_connections and
                    total_connections / total_modules > threshold):
                related_participants = []

                # Collect incoming dependencies (modules that depend on this hub)
                for predecessor in sorted(self.module_dependencies.predecessors(node)):
                    predecessor_file = self.file_paths.get(predecessor, "Unknown")
                    related_participants.append(SmellParticipant(
                        file_path=predecessor_file,
                        element_name=predecessor,
                        element_type="module",
                        role_in_smell="Dependent on Hub",
                    ))

                # Collect outgoing dependencies (modules that the hub depends on)
                for successor in sorted(self.module_dependencies.successors(node)):
                    successor_file = self.file_paths.get(successor, "Unknown")
                    related_participants.append(SmellParticipant(
                        file_path=successor_file,
                        element_name=successor,
                        element_type="module",
                        role_in_smell="Hub Dependency",
                    ))

                # Add external dependencies if any
                for dep_type, dep_name in sorted(self.external_dependencies[node]):
                    related_participants.append(SmellParticipant(
                        file_path="External Library",
                        element_name=dep_name,
                        element_type=dep_type,
                        role_in_smell="External Dependency of Hub",
                    ))

                self.add_smell(
                    "Hub-like Dependency",
                    f"Module '{node}' is a potential hub with {total_connections} project connections "
                    f"(in: {in_degree}, out: {out_degree})",
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

        similarity_threshold = self.thresholds.get('REDUNDANT_ABSTRACTION_SIMILARITY', 0.8)
        for signature, modules in similar_modules.items():
            if len(modules) > 1 and len(signature) >= min_functions:
                # Calculate similarity score between modules
                for i in range(len(modules)):
                    for j in range(i + 1, len(modules)):
                        module1_funcs = self.module_functions[modules[i]]
                        module2_funcs = self.module_functions[modules[j]]
                        similarity = len(module1_funcs & module2_funcs) / \
                            len(module1_funcs | module2_funcs)

                        if similarity >= similarity_threshold:
                            module1, module2 = modules[i], modules[j]
                            common_funcs = module1_funcs & module2_funcs
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
                                    role_in_smell="Duplicated in both modules"
                                ))

                            self.add_smell(
                                "Potential Redundant Abstractions",
                                f"Modules {modules[i]} and {modules[j]} have {similarity:.1%} similar functionalities",
                                self.file_paths.get(modules[i], "Unknown"),
                                modules[i],
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
                for successor in sorted(self.module_dependencies.successors(module)):
                    successor_file = self.file_paths.get(successor, "Unknown")
                    related_participants.append(SmellParticipant(
                        file_path=successor_file,
                        element_name=successor,
                        element_type="module",
                        role_in_smell="Used by God Object"
                    ))

                # Also include function calls to other modules
                for module_called, func_called in sorted(self.function_calls.get(module, [])):
                    related_participants.append(SmellParticipant(
                        file_path=self.file_paths.get(module_called, "Unknown"),
                        element_name=f"{module_called}.{func_called}",
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
        repetition_threshold = self.thresholds.get('IMPROPER_API_USAGE_RATIO', 0.4)

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
                    for other_module, other_calls in sorted(self.api_usage.items()):
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
        # Standalone by design: package markers, entry points, test and build scripts
        excluded_modules = {'__init__', '__main__', 'conftest', 'setup', 'manage',
                            'test', 'tests', 'utils'}
        min_project_size = self.thresholds.get('MIN_PROJECT_SIZE', 3)

        if len(self.module_dependencies.nodes()) < min_project_size:
            return

        for node in self.module_dependencies.nodes():
            if (self.module_dependencies.degree(node) == 0 and
                    not self._has_part(node, excluded_modules) and
                    not node.split('.')[-1].startswith('test_')):
                related_participants = []
                orphan_functions = self.module_functions.get(node, set())

                # Look for modules with similar functionality that could be integrated with this orphan
                for other_node in sorted(self.module_dependencies.nodes()):
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

                    for other_node in sorted(self.module_dependencies.nodes()):
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
        Detect cyclic dependencies: one finding per group of modules that import each
        other directly or indirectly (a strongly connected component), with one
        concrete cycle as evidence. Package __init__ modules are left out.
        """
        max_length = self.thresholds.get('CYCLIC_DEPENDENCY_MAX_LENGTH', 3)
        graph = self._core_graph()

        for component in nx.strongly_connected_components(graph):
            if len(component) < 2:
                continue
            members = sorted(component)
            cycle = [u for u, _ in nx.find_cycle(graph.subgraph(component), source=members[0])]
            cycle_str = ' -> '.join(cycle + [cycle[0]])
            related_participants = [
                SmellParticipant(
                    file_path=self.file_paths.get(member, "Unknown"),
                    element_name=member,
                    element_type="module",
                    role_in_smell="Member of cycle"
                )
                for member in members
            ]

            self.add_smell(
                "Cyclic Dependency",
                f"{len(members)} modules depend on each other cyclically: {', '.join(members[:10])}"
                f"{', ...' if len(members) > 10 else ''}. "
                f"Example cycle: {cycle_str}",
                self.file_paths.get(members[0], "Unknown"),
                members[0],
                severity='high' if len(members) > max_length else 'medium',
                related_participants=related_participants
            )

    def detect_unstable_dependencies(self):
        """
        Detect unstable dependencies (violations of the Stable Dependencies Principle):
        a module that depends on a module which is both unstable, with instability
        Ce / (Ca + Ce) above UNSTABLE_DEPENDENCY_THRESHOLD, and less stable than itself.
        """
        graph = self._core_graph()
        threshold = self.thresholds['UNSTABLE_DEPENDENCY_THRESHOLD']

        def instability(node):
            total = graph.degree(node)
            return graph.out_degree(node) / total if total else 0

        for node in graph.nodes():
            node_instability = instability(node)
            unstable = sorted(dep for dep in graph.successors(node)
                              if instability(dep) > max(threshold, node_instability))
            if unstable:
                min_dependencies = self.thresholds.get("MIN_DEPENDENCIES", 5)
                related_participants = []

                # Track outgoing dependencies (what this module depends on)
                for successor in sorted(self.module_dependencies.successors(node)):
                    successor_file = self.file_paths.get(successor, "Unknown")
                    participant_graph = graph if successor in graph else self.module_dependencies
                    successor_in_degree = participant_graph.in_degree(successor)
                    successor_out_degree = participant_graph.out_degree(successor)
                    successor_total = successor_in_degree + successor_out_degree
                    successor_instability = successor_out_degree / successor_total if successor_total > 0 else 0

                    # Calculate stability comparison
                    stability_comparison = ""
                    if successor_total >= min_dependencies:
                        if successor_instability > node_instability:
                            stability_comparison = " (more unstable)"
                        elif successor_instability < node_instability:
                            stability_comparison = " (more stable)"

                    related_participants.append(SmellParticipant(
                        file_path=successor_file,
                        element_name=successor,
                        element_type="module",
                        role_in_smell=f"Dependency with instability {successor_instability:.2f}{stability_comparison}"
                    ))

                # Also add external dependencies if any
                for dep_type, dep_name in sorted(self.external_dependencies[node]):
                    related_participants.append(SmellParticipant(
                        file_path="External Library",
                        element_name=dep_name,
                        element_type=dep_type,
                        role_in_smell="External Dependency"
                    ))

                self.add_smell(
                    "Unstable Dependency",
                    f"Module '{node}' (instability {node_instability:.2f}) depends on less stable "
                    "modules: " + ', '.join(f"{dep} ({instability(dep):.2f})" for dep in unstable),
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

