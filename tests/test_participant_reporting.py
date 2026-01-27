"""Public reporting contracts consumed by Refactorer and other CSV clients."""

import csv
import dataclasses
import json
import os
import subprocess
import sys

import pytest

from code_quality_analyzer import analyze
from code_quality_analyzer.architectural_smell_detector import (
    ArchitecturalSmell,
    ArchitecturalSmellDetector,
    SmellParticipant,
)
from code_quality_analyzer.code_smell_detector import CodeSmell
from code_quality_analyzer.main import generate_csv_report, generate_report
from code_quality_analyzer.structural_smell_detector import StructuralSmell

CSV_COLUMNS = [
    'Type', 'Name', 'Description', 'File', 'Module/Class', 'Line Number', 'Severity',
    'Related Participants', 'Importers By Participant', 'Participant Dependency Edges',
]


def write_project(root, files):
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')


def finding(detector, name, module=None):
    matches = [s for s in detector.architectural_smells
               if s.name == name and (module is None or s.module_class == module)]
    assert len(matches) == 1, [(s.name, s.module_class) for s in detector.architectural_smells]
    return matches[0]


def sample_smell():
    return ArchitecturalSmell(
        'Example', 'description', 'pkg/primary.py', 'pkg.primary', 3, 'high',
        related_participants=[
            SmellParticipant('pkg/other.py', 'pkg.other.work', 'function', 'Used by café', 10, 20),
            SmellParticipant('pkg/primary.py', 'pkg.primary', 'module', 'Member of cycle'),
        ],
        importers_by_participant={'pkg/other.py': ['client.py', 'pkg/primary.py']},
        participant_dependency_edges=[('pkg/primary.py', 'pkg/other.py')],
    )


def test_dataclass_metadata_defaults_are_independent():
    first = ArchitecturalSmell('one', 'description', 'a.py', 'a')
    second = ArchitecturalSmell('two', 'description', 'b.py', 'b')
    first.related_participants.append(SmellParticipant('a.py', 'a', 'module', 'role'))
    first.importers_by_participant['a.py'] = ['b.py']
    first.participant_dependency_edges.append(('b.py', 'a.py'))
    assert second.related_participants == []
    assert second.importers_by_participant == {}
    assert second.participant_dependency_edges == []
    assert dataclasses.asdict(first.related_participants[0]) == {
        'file_path': 'a.py', 'element_name': 'a', 'element_type': 'module',
        'role_in_smell': 'role', 'start_line': None, 'end_line': None,
    }


def test_csv_preserves_columns_and_json_payloads(tmp_path):
    output = tmp_path / 'report.csv'
    code = CodeSmell('Long Method', 'description', 'code.py', 'Example', 5, 'low')
    structural = StructuralSmell('Large Class', 'description', 'structure.py', 'Example', 6, 'medium')
    generate_csv_report([code], [sample_smell()], [structural], str(output))
    with output.open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == CSV_COLUMNS
        rows = list(reader)
    assert [row['Type'] for row in rows] == ['Structural', 'Code', 'Architectural']
    for row in rows[:2]:
        assert row['Related Participants'] == '[]'
        assert row['Importers By Participant'] == '{}'
        assert row['Participant Dependency Edges'] == '[]'
    row = rows[2]
    assert json.loads(row['Related Participants']) == [
        {'element_name': 'pkg.other.work', 'element_type': 'function',
         'role': 'Used by café', 'file_path': 'pkg/other.py', 'start_line': 10, 'end_line': 20},
        {'element_name': 'pkg.primary', 'element_type': 'module',
         'role': 'Member of cycle', 'file_path': 'pkg/primary.py'},
    ]
    assert json.loads(row['Importers By Participant']) == {
        'pkg/other.py': ['client.py', 'pkg/primary.py'],
    }
    assert json.loads(row['Participant Dependency Edges']) == [['pkg/primary.py', 'pkg/other.py']]
    assert row['Module/Class'] == 'pkg.primary'
    assert row['Line Number'] == '3'


def test_empty_architectural_metadata_is_explicit_json(tmp_path):
    output = tmp_path / 'report.csv'
    generate_csv_report([], [ArchitecturalSmell('Example', 'description', 'a.py', 'a')], [], str(output))
    with output.open(encoding='utf-8', newline='') as handle:
        row, = list(csv.DictReader(handle))
    assert [row[column] for column in CSV_COLUMNS[-3:]] == ['[]', '{}', '[]']


def test_text_report_preserves_participant_roles_and_line_ranges(tmp_path):
    output = tmp_path / 'report.txt'
    generate_report([], [sample_smell()], [], str(output))
    report = output.read_text(encoding='utf-8')
    assert '  Module/Class: pkg.primary\n' in report
    assert ('  Related participants:\n'
            '    1. pkg.other.work (function)\n'
            '       Role: Used by café\n'
            '       File: pkg/other.py\n'
            '       Line: 10 to 20\n') in report
    assert '    2. pkg.primary (module)\n' in report


def test_detector_print_report_preserves_participant_details(capsys):
    detector = ArchitecturalSmellDetector({})
    detector.architectural_smells = [sample_smell()]
    detector.print_report()
    report = capsys.readouterr().out
    assert '1. pkg.other.work (function)' in report
    assert 'Role: Used by café' in report
    assert 'Line: 10 to 20' in report


def test_cycle_reports_entire_component_and_external_importers(architectural_smell_detector, tmp_path):
    # No simple cycle contains all four modules: a<->b and b<->c<->d.
    files = {
        'a.py': 'import b\n', 'b.py': 'import a\nimport c\n',
        'c.py': 'import b\nimport d\n', 'd.py': 'import c\n',
        'client.py': 'import a\n',
    }
    write_project(tmp_path, files)
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'Cyclic Dependency')
    assert {p.element_name for p in smell.related_participants} == {'a', 'b', 'c', 'd'}
    assert {p.role_in_smell for p in smell.related_participants} == {'Member of cycle'}
    assert len(smell.related_participants) == 4
    def path(name):
        return str(tmp_path / f'{name}.py')
    assert smell.importers_by_participant == {
        path('a'): sorted([path('b'), path('client')]),
        path('b'): sorted([path('a'), path('c')]),
        path('c'): sorted([path('b'), path('d')]),
        path('d'): [path('c')],
    }
    assert smell.participant_dependency_edges == sorted([
        (path('a'), path('b')), (path('b'), path('a')), (path('b'), path('c')),
        (path('c'), path('b')), (path('c'), path('d')), (path('d'), path('c')),
    ])


def test_metadata_uses_files_and_excludes_nonparticipants(architectural_smell_detector, tmp_path):
    write_project(tmp_path, {
        'a.py': 'import b\n', 'b.py': 'import a\n', 'client.py': 'import a\n',
    })
    detector = architectural_smell_detector
    detector.analyze_directory(str(tmp_path))
    a, b, client = (str(tmp_path / name) for name in ['a.py', 'b.py', 'client.py'])
    participants = [
        SmellParticipant(a, 'a.work', 'function', 'role'),
        SmellParticipant(a, 'a.other', 'function', 'role'),
        SmellParticipant(b, 'arbitrary_display_name', 'module', 'role'),
        SmellParticipant('Unknown', 'unknown', 'module', 'role'),
        SmellParticipant('External Library', 'thirdparty', 'third_party', 'role'),
        SmellParticipant(str(tmp_path / 'not_project.py'), 'missing', 'module', 'role'),
    ]
    detector.add_smell('Example', 'description', client, 'client', related_participants=participants)
    smell = detector.architectural_smells[-1]
    assert smell.importers_by_participant == {a: sorted([b, client]), b: [a]}
    assert smell.participant_dependency_edges == sorted([(a, b), (b, a)])


def test_nested_relative_imports_keep_full_participant_names(architectural_smell_detector, tmp_path):
    write_project(tmp_path, {
        'pkg/__init__.py': '', 'pkg/deep/__init__.py': '',
        'pkg/deep/a.py': 'from . import b\n', 'pkg/deep/b.py': 'from . import a\n',
        'client.py': 'from pkg.deep import a\n',
    })
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'Cyclic Dependency')
    assert {p.element_name for p in smell.related_participants} == {'pkg.deep.a', 'pkg.deep.b'}
    a, b = (str(tmp_path / 'pkg/deep' / name) for name in ['a.py', 'b.py'])
    assert smell.participant_dependency_edges == sorted([(a, b), (b, a)])
    assert smell.importers_by_participant[a] == sorted([b, str(tmp_path / 'client.py')])


@pytest.mark.parametrize('calls', [
    'import support\nsupport.helper()\nsupport.helper()\n',
    'import support as service\nservice.helper()\nservice.helper()\n',
    'from support import helper as run\nrun()\nrun()\n',
])
def test_god_object_keeps_module_and_function_participants(architectural_smell_detector, tmp_path, calls):
    write_project(tmp_path, {
        'support.py': 'def helper(): pass\n',
        'large.py': calls + '\n'.join(f'def work_{i}(): pass' for i in range(26)),
    })
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'God Object', 'large')
    assert [(p.element_name, p.element_type, p.role_in_smell) for p in smell.related_participants] == [
        ('support', 'module', 'Used by God Object'),
        ('support.helper', 'function', 'Function called by God Object'),
    ]
    assert smell.importers_by_participant == {str(tmp_path / 'support.py'): [str(tmp_path / 'large.py')]}
    assert smell.participant_dependency_edges == []


@pytest.mark.parametrize('import_statement', ['from pkg import support', 'from . import support'])
@pytest.mark.parametrize(('support_source', 'expected_functions'), [
    ('def helper(): pass\n', ['pkg.support.helper']),
    ('def other(): pass\n', []),
])
def test_function_participants_use_resolved_import_not_package_fallback(
        architectural_smell_detector, tmp_path, import_statement, support_source, expected_functions):
    write_project(tmp_path, {
        'pkg/__init__.py': 'def helper(): pass\n',
        'pkg/support.py': support_source,
        'pkg/large.py': import_statement + '\nsupport.helper()\n'
        + '\n'.join(f'def work_{i}(): pass' for i in range(26)),
    })
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'God Object', 'pkg.large')
    functions = [p.element_name for p in smell.related_participants if p.element_type == 'function']
    assert functions == expected_functions


def test_scattered_functionality_reports_each_module(architectural_smell_detector, tmp_path):
    write_project(tmp_path, {f'module{i}.py': 'def shared_work(): pass\n' for i in range(3)})
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'Scattered Functionality')
    assert {p.element_name for p in smell.related_participants} == {'module0', 'module1', 'module2'}
    assert {p.role_in_smell for p in smell.related_participants} == {
        "Contains implementation of 'shared_work' function",
    }
    assert smell.importers_by_participant == {str(tmp_path / f'module{i}.py'): [] for i in range(3)}
    assert smell.participant_dependency_edges == []


def test_redundant_abstractions_keep_module_and_function_roles(architectural_smell_detector, tmp_path):
    source = '\n'.join(f'def function_{i}(): pass' for i in range(5))
    write_project(tmp_path, {'first.py': source, 'second.py': source})
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'Potential Redundant Abstractions')
    module, *functions = smell.related_participants
    assert module.element_type == 'module'
    assert module.role_in_smell == f'Redundant with {smell.module_class} (similarity: 100.0%)'
    assert {p.element_name for p in functions} == {f'{smell.module_class}.function_{i}' for i in range(5)}
    assert {p.role_in_smell for p in functions} == {'Duplicated in both modules'}


def test_improper_api_usage_keeps_api_call_participants(architectural_smell_detector, tmp_path):
    write_project(tmp_path, {'repetitive.py': 'api.action()\n' * 10})
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'Potential Improper API Usage')
    participant, = smell.related_participants
    assert (participant.element_name, participant.element_type, participant.role_in_smell) == (
        'action', 'api_call', 'Repetitive API call (10 occurrences)',
    )


def test_api_usage_keeps_reference_module(architectural_smell_detector, tmp_path):
    write_project(tmp_path, {
        'repetitive.py': 'api.action()\n' * 10,
        'reference.py': 'api.action()\n' * 3 + '\n'.join(f'api.other{i}()' for i in range(7)),
    })
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'Potential Improper API Usage', 'repetitive')
    api_call, reference = smell.related_participants
    assert api_call.element_type == 'api_call'
    assert (reference.element_name, reference.role_in_smell) == (
        'reference', 'Reference module with better API usage pattern',
    )


def test_orphan_module_keeps_integration_candidates(architectural_smell_detector, tmp_path):
    write_project(tmp_path, {
        'orphan.py': 'def shared_work(): pass\n',
        'first.py': 'import second\ndef shared_work(): pass\n',
        'second.py': 'def other_work(): pass\n',
    })
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'Orphan Module', 'orphan')
    participant, = smell.related_participants
    assert participant.element_name == 'first'
    assert participant.role_in_smell == 'Potential integration candidate (shares 1 similar functions)'


def test_orphan_module_keeps_same_directory_fallback(architectural_smell_detector, tmp_path):
    write_project(tmp_path, {
        'orphan.py': 'def independent(): pass\n',
        'first.py': 'import second\ndef first_work(): pass\n',
        'second.py': 'def second_work(): pass\n',
    })
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'Orphan Module', 'orphan')
    assert {(p.element_name, p.role_in_smell) for p in smell.related_participants} == {
        ('first', 'Potential integration candidate (same directory)'),
        ('second', 'Potential integration candidate (same directory)'),
    }


def test_hub_keeps_incoming_and_outgoing_roles(architectural_smell_detector, tmp_path):
    files = {f'client{i}.py': 'import hub\n' for i in range(3)}
    files.update({f'worker{i}.py': 'def work(): pass\n' for i in range(3)})
    files['hub.py'] = '\n'.join(f'import worker{i}' for i in range(3)) + '\nimport os\nimport requests\n'
    write_project(tmp_path, files)
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'Hub-like Dependency', 'hub')
    assert {(p.element_name, p.role_in_smell) for p in smell.related_participants} == {
        *((f'client{i}', 'Dependent on Hub') for i in range(3)),
        *((f'worker{i}', 'Hub Dependency') for i in range(3)),
        ('os', 'External Dependency of Hub'),
        ('requests', 'External Dependency of Hub'),
    }
    assert len(smell.related_participants) == 8
    external = [p for p in smell.related_participants if p.file_path == 'External Library']
    assert {(p.element_name, p.element_type) for p in external} == {('os', 'stdlib'), ('requests', 'third_party')}
    assert 'External Library' not in smell.importers_by_participant


def test_unstable_dependency_keeps_dependency_roles(architectural_smell_detector, tmp_path):
    files = {f'client{i}.py': 'import stable\n' for i in range(5)}
    files.update({f'leaf{i}.py': 'def work(): pass\n' for i in range(5)})
    files['stable.py'] = 'import volatile\n'
    files['volatile.py'] = '\n'.join(f'import leaf{i}' for i in range(5))
    write_project(tmp_path, files)
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'Unstable Dependency', 'stable')
    participant, = smell.related_participants
    assert (participant.element_name, participant.element_type, participant.role_in_smell) == (
        'volatile', 'module', 'Dependency with instability 0.83 (more unstable)',
    )


def test_dependency_role_uses_same_instability_as_finding(architectural_smell_detector, tmp_path):
    files = {f'client{i}.py': 'from pkg import stable\n' for i in range(5)}
    files.update({f'pkg/leaf{i}.py': 'def work(): pass\n' for i in range(5)})
    files['pkg/__init__.py'] = 'from . import volatile\n'
    files['pkg/stable.py'] = 'from . import volatile\n'
    files['pkg/volatile.py'] = '\n'.join(f'from . import leaf{i}' for i in range(5))
    write_project(tmp_path, files)
    architectural_smell_detector.detect_smells(str(tmp_path))
    smell = finding(architectural_smell_detector, 'Unstable Dependency', 'pkg.stable')
    # Package __init__ imports are excluded when deciding instability. Participant
    # roles must use the same measurement as the finding, not the raw import graph.
    assert 'pkg.volatile (0.83)' in smell.description
    participant, = smell.related_participants
    assert participant.element_name == 'pkg.volatile'
    assert participant.role_in_smell == 'Dependency with instability 0.83 (more unstable)'


def test_json_api_contains_architectural_metadata(tmp_path):
    write_project(tmp_path, {'a.py': 'import b\n', 'b.py': 'import a\n'})
    report = analyze(str(tmp_path), types=['architectural'])
    assert report['errors'] == []
    smell, = [s for s in report['findings'] if s['name'] == 'Cyclic Dependency']
    assert {p['element_name'] for p in smell['related_participants']} == {'a', 'b'}
    assert all(p['role_in_smell'] == 'Member of cycle' for p in smell['related_participants'])
    assert smell['importers_by_participant']
    assert len(smell['participant_dependency_edges']) == 2
    assert json.loads(json.dumps(report))['schema_version'] == 1


def test_reporting_metadata_is_stable_across_hash_seeds(tmp_path):
    source = '\n'.join(f'def function_{i}(): pass' for i in range(5))
    write_project(tmp_path, {
        'a.py': 'import b\n' + source, 'b.py': 'import a\nimport c\n' + source,
        'c.py': 'import b\n' + source,
    })
    command = [sys.executable, '-c',
               'import json,sys; from code_quality_analyzer import analyze; '
               'print(json.dumps(analyze(sys.argv[1], types=["architectural"])))', str(tmp_path)]
    outputs = [subprocess.check_output(command, text=True, env={**os.environ, 'PYTHONHASHSEED': seed})
               for seed in ['1', '2', '19']]
    assert outputs[0] == outputs[1] == outputs[2]
