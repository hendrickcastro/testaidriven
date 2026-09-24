from aidriven.domain.models import ArtifactKind
from aidriven.services.artifacts import extract_files


def test_explicit_file_convention() -> None:
    text = "Here:\n```python file=solver.py\nprint(1)\n```\n```html file=index.html\n<p>x</p>\n```\n"
    files = {f.name: f for f in extract_files(text)}
    assert files["solver.py"].content == "print(1)"
    assert files["solver.py"].explicit
    assert files["index.html"].kind == ArtifactKind.HTML


def test_filename_on_previous_line() -> None:
    text = "**solver.py**\n```python\nx = 1\n```"
    files = extract_files(text)
    assert files[0].name == "solver.py"
    assert not files[0].explicit


def test_first_line_comment() -> None:
    text = "```python\n# file: lisp.py\nx = 2\n```"
    assert extract_files(text)[0].name == "lisp.py"


def test_single_expected_file_fallback_uses_last_matching_block() -> None:
    text = "```python\nfirst = 1\n```\nbetter:\n```python\nsecond = 2\n```"
    files = extract_files(text, ["solver.py"])
    assert files[0].name == "solver.py"
    assert files[0].content == "second = 2"


def test_path_traversal_names_are_flattened() -> None:
    text = "```python file=../../evil.py\nx=1\n```"
    assert extract_files(text)[0].name == "evil.py"


def test_last_block_with_same_name_wins() -> None:
    text = "```python file=a.py\nv1\n```\n```python file=a.py\nv2\n```"
    assert extract_files(text)[0].content == "v2"
