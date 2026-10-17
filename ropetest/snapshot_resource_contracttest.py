"""Real source snapshots keep local and external resource reads closed."""

import os
import shutil
import sys
from collections.abc import Mapping

import pytest

from rope.base import codeanalyze, exceptions, libutils, pyobjects
from rope.base.fscommands import FileSystemCommands
from rope.base.project import NoProject, Project, get_no_project
from rope.refactor import patchedast, restructure, similarfinder, wildcards


@pytest.mark.parametrize(
    "source, pattern, goal, expected",
    [
        ("value = 1 + 1\n", "1 + 1", "2", "value = 2\n"),
        ("value = 1\n", "value = 1", "value = 2", "value = 2\n"),
        ("value = 1\n", "other = 1", "other = 2", None),
    ],
)
def test_public_change_computer_preserves_native_source_changes(
    source, pattern, goal, expected
):
    tree = patchedast.get_patched_ast(source)
    finder = similarfinder.RawSimilarFinder(source, tree)
    computer = restructure.ChangeComputer(
        source,
        tree,
        codeanalyze.SourceLinesAdapter(source),
        similarfinder.CodeTemplate(goal),
        list(finder.get_matches(pattern)),
    )
    assert computer.get_changed() == expected
    assert restructure.replace(source, pattern, goal) == (
        source if expected is None else expected
    )


def test_public_change_computer_preserves_native_missing_name_error():
    source = "value = 1\n"
    tree = patchedast.get_patched_ast(source)
    finder = similarfinder.RawSimilarFinder(source, tree)
    computer = restructure.ChangeComputer(
        source,
        tree,
        codeanalyze.SourceLinesAdapter(source),
        similarfinder.CodeTemplate("value = ${missing}"),
        list(finder.get_matches("value = ${number}")),
    )
    with pytest.raises(similarfinder.BadNameInCheckError, match="Unknown name <missing>"):
        computer.get_changed()
    with pytest.raises(similarfinder.BadNameInCheckError, match="Unknown name <missing>"):
        restructure.replace(source, "value = ${number}", "value = ${missing}")


def test_restructure_preserves_custom_wildcard_payloads(tmp_path):
    project = Project(
        str(tmp_path),
        ropefolder=None,
        save_objectdb=False,
        save_history=False,
    )
    try:
        default = wildcards.DefaultWildcard(project)

        class Alternatives:
            def matches(self, suspect, arguments):
                return any(default.matches(suspect, argument) for argument in arguments)

        source = (
            "def first():\n    pass\n"
            "def second():\n    pass\n"
            "def third():\n    pass\n"
            "first()\nsecond()\nthird()\n"
        )
        resource = project.root.create_file("consumer.py")
        resource.write(source)
        refactoring = restructure.Restructure(
            project,
            "${call}()",
            "renamed()",
            args={
                "call": (
                    "alternatives",
                    ("name=consumer.first", "name=consumer.second"),
                )
            },
            wildcards={"default": default, "alternatives": Alternatives()},
        )
        project.do(refactoring.get_changes(resources=[resource]))
        assert resource.read() == source.replace(
            "first()\nsecond()\nthird()\n", "renamed()\nrenamed()\nthird()\n"
        )
    finally:
        project.close()


def test_loaded_stdlib_alias_preserves_native_base_and_declared_preferences(tmp_path):
    (tmp_path / "consumer.py").write_text(
        "from collections.abc import Mapping\nclass Consumer(Mapping):\n    pass\n",
        encoding="utf-8",
    )
    project = Project(
        str(tmp_path),
        ropefolder=None,
        source_folders=[""],
        extension_modules=["builtins"],
        import_dynload_stdmods=True,
        save_objectdb=False,
        save_history=False,
    )
    try:
        declared = tuple(project.prefs.get("extension_modules"))
        module = project.get_module("collections.abc")
        mapping = module["Mapping"].get_object()
        owner = project.get_module("consumer")["Consumer"].get_object()
        assert mapping in owner.get_superclasses()
        assert mapping.get_name() == Mapping.__name__
        assert module is project.get_module("collections.abc")
        assert tuple(project.prefs.get("extension_modules")) == declared
        assert project.pycore.builtin_module("rope.base.pycore") is None
    finally:
        project.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_frozen_admission_uses_the_existing_extension_policy_snapshot(tmp_path, enabled):
    captured = CapturedFileSystem((str(tmp_path),))
    project = Project(
        str(tmp_path),
        fscommands=captured,
        external_project=NoProject(fscommands=captured),
        ropefolder=None,
        source_folders=[""],
        extension_modules=["builtins"],
        import_dynload_stdmods=enabled,
        save_objectdb=False,
        save_history=False,
    )
    try:
        project.get_pycore().extension_modules
        project.set("import_dynload_stdmods", not enabled)
        if enabled:
            assert project.get_module("_frozen_importlib").get_resource() is None
        else:
            with pytest.raises(exceptions.ModuleNotFoundError):
                project.get_module("_frozen_importlib")
    finally:
        project.close()


def test_source_module_created_after_stdlib_lookup_takes_precedence(tmp_path):
    project = Project(
        str(tmp_path),
        ropefolder=None,
        source_folders=[""],
        extension_modules=["builtins"],
        import_dynload_stdmods=True,
        save_objectdb=False,
        save_history=False,
    )
    try:
        project.get_module("collections.abc")["Mapping"].get_object()
        package = project.root.create_folder("collections")
        package.create_file("__init__.py")
        source = package.create_file("abc.py")
        source.write("class OwnedSource:\n    pass\n")
        resolved = project.get_module("collections.abc")
        assert resolved.get_resource() == source
        assert resolved["OwnedSource"].get_object().get_name() == "OwnedSource"
    finally:
        project.close()


def test_explicit_folder_source_precedes_loaded_stdlib_alias(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    external = tmp_path / "external"
    package = external / "collections"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    source = package / "abc.py"
    source.write_text("class OwnedSource:\n    pass\n", encoding="utf-8")
    captured = CapturedFileSystem((str(root), str(external)))
    outside = NoProject(fscommands=captured)
    project = Project(
        str(root),
        fscommands=captured,
        external_project=outside,
        ropefolder=None,
        source_folders=[""],
        extension_modules=["builtins"],
        import_dynload_stdmods=True,
        save_objectdb=False,
        save_history=False,
    )
    try:
        folder = outside.get_resource(str(external))
        assert project.find_module("collections.abc", folder).real_path == str(source)
        resolved = project.get_module("collections.abc", folder)
        assert resolved.get_resource().real_path == str(source)
        assert resolved["OwnedSource"].get_object().get_name() == "OwnedSource"
    finally:
        project.close()


@pytest.mark.parametrize("level", [1, 2])
@pytest.mark.parametrize("name", ["", "owner"])
def test_relative_module_without_folder_has_native_missing_error(tmp_path, name, level):
    (tmp_path / "owner.py").write_text("class Owner:\n    pass\n", encoding="utf-8")
    project = Project(
        str(tmp_path),
        ropefolder=None,
        save_objectdb=False,
        save_history=False,
    )
    try:
        with pytest.raises(exceptions.ModuleNotFoundError):
            project.get_relative_module(name, None, level)
        module = libutils.get_string_module(project, "from .owner import Owner\n")
        assert module["Owner"].get_object() is pyobjects.get_unknown()
    finally:
        project.close()


@pytest.mark.parametrize("source_exists", [False, True])
@pytest.mark.parametrize("declared", [False, True])
def test_loaded_module_missing_spec_preserves_source_and_native_errors(
    tmp_path, source_exists, declared
):
    if source_exists:
        package = tmp_path / "collections"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "abc.py").write_text("class OwnedSource:\n    pass\n", encoding="utf-8")
    captured = CapturedFileSystem((str(tmp_path),))
    project = Project(
        str(tmp_path),
        fscommands=captured,
        external_project=NoProject(fscommands=captured),
        ropefolder=None,
        source_folders=[""],
        extension_modules=["builtins", "collections.abc"] if declared else ["builtins"],
        import_dynload_stdmods=True,
        save_objectdb=False,
        save_history=False,
    )
    module = sys.modules["collections.abc"]
    spec = module.__spec__
    del module.__spec__
    try:
        if declared:
            resolved = project.get_module("collections.abc")
            assert resolved["Mapping"].get_object().get_name() == Mapping.__name__
        elif source_exists:
            resolved = project.get_module("collections.abc")
            assert resolved["OwnedSource"].get_object().get_name() == "OwnedSource"
        else:
            with pytest.raises(AttributeError, match="__spec__"):
                project.get_module("collections.abc")
    finally:
        module.__spec__ = spec
        project.close()


class CapturedFileSystem(FileSystemCommands):
    """A read-only filesystem captured from real test inputs."""

    def __init__(self, roots):
        self.files = {}
        self.directories = {}
        self.states = {}
        for root in roots:
            for directory, _folders, files in os.walk(root):
                self.directories[directory] = tuple(os.listdir(directory))
                self.states[directory] = os.stat(directory)
                for name in files:
                    path = os.path.join(directory, name)
                    self.states[path] = os.stat(path)
                    with open(path, "rb") as stream:
                        self.files[path] = stream.read()

    @staticmethod
    def realpath(path):
        return os.path.abspath(os.path.expanduser(path))

    def exists(self, path):
        return path in self.files or path in self.directories

    def isfile(self, path):
        return path in self.files

    def isdir(self, path):
        return path in self.directories

    def listdir(self, path):
        return self.directories[path]

    def read(self, path):
        return self.files[path]

    def stat(self, path):
        return self.states[path]


def test_local_and_external_snapshot_resources_ignore_changed_disk(tmp_path):
    root = tmp_path / "consumer"
    local = root / "src"
    external = tmp_path / "dependencies"
    package = external / "provider"
    local.mkdir(parents=True)
    package.mkdir(parents=True)
    alias = "u\u0301"
    (local / "consumer.py").write_text(
        f"from provider import Original as {alias}\n"
        f"borrowed = {alias}\n"
        "def collect(values):\n"
        f"    marker = 'π'; result = [({alias}, item) for item in values]\n"
        "    return result\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "class Original:\n    marker = 1\n",
        encoding="utf-8",
    )
    captured = CapturedFileSystem((str(root), str(external)))
    singleton = get_no_project()
    shutil.rmtree(root)
    shutil.rmtree(external)
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "class Changed:\n    marker = 2\n",
        encoding="utf-8",
    )
    (external / "late.py").write_text("marker = 3\n", encoding="utf-8")
    outside = NoProject(fscommands=captured)
    project = Project(
        str(root),
        fscommands=captured,
        external_project=outside,
        ropefolder=None,
        source_folders=["src"],
        python_path=[str(external)],
        save_objectdb=False,
        save_history=False,
    )
    try:
        module = project.get_module("consumer")
        name = module.get_attribute("borrowed")
        assert name.get_object().get_name() == "Original"
        scope = module.get_scope()
        comprehension = scope.get_scopes()[0].get_scopes()[0]
        offset = module.source_code.rindex(alias)
        assert scope.get_inner_scope_for_offset(offset) is comprehension
        assert comprehension.lookup("ú") is scope.lookup("ú")
        assert comprehension.lookup("ú").get_object() is name.get_object()
        holder = name.get_object().get_module()
        assert holder.get_resource().project is outside
        assert project.find_module("late") is None
        assert project.get_resource("src/consumer.py").read().startswith(
            "from provider import Original"
        )
        assert project.get_folder("src").get_children() == [
            project.get_file("src/consumer.py")
        ]
        assert get_no_project() is singleton
        assert not root.exists()
    finally:
        project.close()


def test_snapshot_directory_acquisition_failure_is_not_an_empty_result(tmp_path):
    root = tmp_path / "consumer"
    root.mkdir()
    captured = CapturedFileSystem((str(root),))
    project = Project(
        str(root),
        fscommands=captured,
        external_project=NoProject(fscommands=captured),
        ropefolder=None,
        save_objectdb=False,
        save_history=False,
    )
    try:
        del captured.directories[str(root)]
        with pytest.raises(KeyError):
            project.get_folder("").get_children()
    finally:
        project.close()


def test_native_relative_namespace_and_stub_resources_use_captured_inputs(tmp_path):
    root = tmp_path / "consumer"
    local = root / "src"
    package = local / "application"
    external = tmp_path / "dependencies"
    namespace = external / "namespace"
    package.mkdir(parents=True)
    namespace.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "owner.py").write_text("class Local:\n    pass\n", encoding="utf-8")
    (package / "consumer.py").write_text(
        "from .owner import Local\n"
        "from namespace.owner import External\n"
        "local = Local\nexternal = External\n",
        encoding="utf-8",
    )
    (namespace / "owner.py").write_text(
        "class External:\n    pass\n", encoding="utf-8"
    )
    (local / "schema.pyi").write_text(
        "class Schema:\n    value: str\n", encoding="utf-8"
    )
    captured = CapturedFileSystem((str(root), str(external)))
    shutil.rmtree(root)
    shutil.rmtree(external)
    outside = NoProject(fscommands=captured)
    project = Project(
        str(root),
        fscommands=captured,
        external_project=outside,
        ropefolder=None,
        source_folders=["src"],
        python_path=[str(external)],
        save_objectdb=False,
        save_history=False,
    )
    try:
        consumer = project.get_module("application.consumer")
        relative = project.find_relative_module(
            "owner", project.get_folder("src/application"), 1
        )
        assert relative == project.get_resource("src/application/owner.py")
        local_owner = consumer.get_attribute("local").get_object()
        assert local_owner.get_name() == "Local"
        assert local_owner.get_module().get_resource() == relative
        external_owner = consumer.get_attribute("external").get_object()
        assert external_owner.get_name() == "External"
        assert external_owner.get_module().get_resource().project is outside
        assert project.find_module("namespace").is_folder()
        stub = project.get_resource("src/schema.pyi")
        assert project.get_pymodule(stub).get_attribute("Schema").get_object().get_name() == "Schema"
        # This port preserves Rope's existing module election: .pyi is an
        # explicitly supplied resource, not an extra module-name fallback.
        assert project.find_module("schema") is None
        assert not root.exists()
        assert not external.exists()
    finally:
        project.close()
