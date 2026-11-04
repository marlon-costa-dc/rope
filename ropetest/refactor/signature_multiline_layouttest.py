"""Public signature additions preserve multiline source and runtime binding."""

import inspect
from textwrap import dedent

import pytest

from rope.base.project import Project
from rope.refactor.change_signature import ArgumentAdder, ChangeSignature


@pytest.mark.parametrize(
    "header, call, expected_header",
    [
        (
            "def run(\n    value: int,\n) -> int:",
            "run(7)",
            "def run(\n    value: int,\n    *,\n    observer: object | None = None,\n) -> int:",
        ),
        (
            "def run(\n  value: int,  # retained Unicode café\n) -> int:",
            "run(7)",
            "def run(\n  value: int,  # retained Unicode café\n  *,\n  observer: object | None = None,\n) -> int:",
        ),
        (
            "def run(\n    value: int  # retained separator position\n) -> int:",
            "run(7)",
            "def run(\n    value: int,  # retained separator position\n    *,\n    observer: object | None = None,\n) -> int:",
        ),
        (
            "def run(\n    value: int,\n    **options: str\n) -> int:",
            "run(7, label='same')",
            "def run(\n    value: int,\n    *,\n    observer: object | None = None,\n    **options: str\n) -> int:",
        ),
    ],
)
def test_optional_keyword_retains_multiline_layout(tmp_path, header, call, expected_header):
    project = Project(str(tmp_path), ropefolder=None)
    try:
        resource = project.root.create_file("sample.py")
        original = header + "\n    return value\n\nanswer = " + call + "\n"
        resource.write(original)
        previous = {}
        exec(compile(original, resource.path, "exec"), previous)
        addition = ArgumentAdder(
            1, "observer", default="None", annotation="object | None", keyword_only=True
        )
        project.do(
            ChangeSignature(project, resource, original.index("run")).get_changes([addition])
        )
        changed = resource.read()
        assert changed.startswith(expected_header + "\n")
        assert changed.endswith("answer = " + call + "\n")
        current = {}
        exec(compile(changed, resource.path, "exec"), current)
        assert current["answer"] == previous["answer"] == 7
        parameter = inspect.signature(current["run"]).parameters["observer"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None
        assert not ChangeSignature(
            project, resource, changed.index("run")
        ).get_changes([addition]).changes
    finally:
        project.close()


def test_required_keyword_retains_multiline_call_and_comment(tmp_path):
    project = Project(str(tmp_path), ropefolder=None)
    try:
        resource = project.root.create_file("sample.py")
        resource.write(
            dedent(
                """\
                def run(value: int) -> int:
                    return value

                answer = run(
                    7  # retained call comment
                )
                """
            )
        )
        addition = ArgumentAdder(1, "token", value="'received'", keyword_only=True)
        project.do(
            ChangeSignature(project, resource, resource.read().index("run")).get_changes(
                [addition]
            )
        )
        changed = resource.read()
        assert "    7,  # retained call comment\n    token='received',\n)" in changed
        current = {}
        exec(compile(changed, resource.path, "exec"), current)
        assert current["answer"] == 7
    finally:
        project.close()


def test_required_keyword_retains_multiline_generator_runtime(tmp_path):
    project = Project(str(tmp_path), ropefolder=None)
    try:
        resource = project.root.create_file("sample.py")
        resource.write(
            "def run(values):\n    return list(values)\n\n"
            "answer = run(\n    number for number in range(3)\n)\n"
        )
        addition = ArgumentAdder(1, "token", value="'received'", keyword_only=True)
        project.do(
            ChangeSignature(project, resource, resource.read().index("run")).get_changes(
                [addition]
            )
        )
        current = {}
        exec(compile(resource.read(), resource.path, "exec"), current)
        assert current["answer"] == [0, 1, 2]
    finally:
        project.close()


def test_empty_multiline_header_uses_the_project_indentation(tmp_path):
    project = Project(str(tmp_path), ropefolder=None, indent_size=2)
    try:
        resource = project.root.create_file("sample.py")
        resource.write("def run(\n):\n  return len('value')\n\nanswer = run()\n")
        addition = ArgumentAdder(0, "token", default="None", keyword_only=True)
        project.do(
            ChangeSignature(project, resource, resource.read().index("run")).get_changes(
                [addition]
            )
        )
        changed = resource.read()
        assert changed.startswith("def run(\n  *,\n  token = None,\n):\n")
        current = {}
        exec(compile(changed, resource.path, "exec"), current)
        assert current["answer"] == 5
    finally:
        project.close()


def test_multiline_header_preserves_the_original_file_newlines(tmp_path):
    project = Project(str(tmp_path), ropefolder=None)
    try:
        resource = project.root.create_file("sample.py")
        resource.write("def run(\r\n    value: int,\r\n) -> int:\r\n    return value\r\n")
        addition = ArgumentAdder(1, "token", default="None", keyword_only=True)
        project.do(
            ChangeSignature(project, resource, resource.read().index("run")).get_changes(
                [addition]
            )
        )
        assert b"\n" not in resource.read_bytes().replace(b"\r\n", b"")
        current = {}
        exec(compile(resource.read(), resource.path, "exec"), current)
        assert current["run"](7) == 7
    finally:
        project.close()
