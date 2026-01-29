from code_quality_analyzer.path_filters import (
    compile_pathspec,
    filter_child_directories,
    should_ignore,
)


def test_compile_pathspec_handles_inline_and_file(tmp_path):
    pattern_file = tmp_path / "patterns.txt"
    pattern_file.write_text("# comment\nignored_dir/\n")

    pathspec = compile_pathspec(["*.tmp"], [str(pattern_file)])

    assert pathspec is not None

    base_dir = tmp_path
    ignored_dir = tmp_path / "ignored_dir"
    ignored_dir.mkdir()
    ignored_file = tmp_path / "notes.tmp"
    ignored_file.write_text("temp")

    assert should_ignore(pathspec, str(base_dir), str(ignored_dir))
    assert should_ignore(pathspec, str(base_dir), str(ignored_file))


def test_filter_child_directories_skips_ignored_entries(tmp_path):
    (tmp_path / "skip_me").mkdir()
    (tmp_path / "keep_me").mkdir()

    pathspec = compile_pathspec(["skip_me/"])
    dirs = ["skip_me", "keep_me"]

    filter_child_directories(dirs, str(tmp_path), str(tmp_path), pathspec)

    assert dirs == ["keep_me"]
