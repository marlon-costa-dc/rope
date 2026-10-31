"""A module containing classes for move refactoring

`create_move()` is a factory for creating move refactoring objects
based on inputs.

"""

from __future__ import annotations

import keyword
import typing
from typing import List, Optional, Union

from rope.base import (
    ast,
    codeanalyze,
    evaluate,
    exceptions,
    libutils,
    pynames,
    pyobjects,
    taskhandle,
    worder,
)
from rope.base.change import ChangeContents, ChangeSet, MoveResource
from rope.refactor import functionutils, importutils, occurrences, rename, sourceutils
from rope.refactor.importutils import importinfo
from rope.refactor.importutils.module_imports import (
    get_first_decorator_or_function_start_line,
)

if typing.TYPE_CHECKING:
    from rope.base import project, resources


def create_move(project, resource, offset=None):
    """A factory for creating Move objects

    Based on `resource` and `offset`, return one of `MoveModule`,
    `MoveGlobal` or `MoveMethod` for performing move refactoring.

    """
    if offset is None:
        return MoveModule(project, resource)
    this_pymodule = project.get_pymodule(resource)
    pyname = evaluate.eval_location(this_pymodule, offset)
    if pyname is not None:
        pyobject = pyname.get_object()
        if isinstance(pyobject, pyobjects.PyModule) or isinstance(
            pyobject, pyobjects.PyPackage
        ):
            return MoveModule(project, pyobject.get_resource())
        if isinstance(pyobject, pyobjects.PyFunction) and isinstance(
            pyobject.parent, pyobjects.PyClass
        ):
            return MoveMethod(project, resource, offset)
        if (
            isinstance(pyobject, pyobjects.PyDefinedObject)
            and isinstance(pyobject.parent, pyobjects.PyModule)
            or isinstance(pyname, pynames.AssignedName)
        ):
            return MoveGlobal(project, resource, offset)
    raise exceptions.RefactoringError(
        "Move only works on global classes/functions/variables, modules and methods."
    )


class MoveMethod:
    """For moving methods

    It makes a new method in the destination class and changes
    the body of the old method to call the new method.  You can
    inline the old method to change all of its occurrences.

    ``get_class_changes()`` explicitly transfers static and class methods
    together to an existing source class, without forwarding methods. It
    preserves their declarations and rewrites proven references in the
    selected resources. Unresolved references, overrides, destination
    collisions and dependencies on the old class reject the whole plan.
    Planning never writes resources; callers apply its single ChangeSet.

    """

    def __init__(self, project, resource, offset):
        self.project = project
        this_pymodule = self.project.get_pymodule(resource)
        pyname = evaluate.eval_location(this_pymodule, offset)
        self.method_name = worder.get_name_at(resource, offset)
        self.pyfunction = pyname.get_object()
        if self.pyfunction.get_kind() != "method":
            raise exceptions.RefactoringError("Only normal methods can be moved.")

    @classmethod
    def get_class_changes(
        cls,
        project,
        moves,
        destination_resource,
        destination_offset,
        resources=None,
        task_handle=taskhandle.DEFAULT_TASK_HANDLE,
    ):
        """Transfer decorated methods to an existing class in one ChangeSet.

        ``moves`` contains ``(resource, offset, new_name)`` triples. The
        destination offset identifies a source class. Only native static
        and class methods with no dependency on the former receiver are
        admitted. References between transferred methods retain ``cls`` only
        when it resolves to the exact lexical receiver. Every rewritten
        destination is proven in its consumer's native lexical environment.
        Private identifiers must retain their effective lexical spelling;
        implicit construction and subscription slots are not transferable.
        Locally defined bindings retain their native lexical identity without
        requiring inference of their values. Global and imported bindings
        require resolved values. A private declaration whose effective name
        is not closed by the native occurrence search is rejected.
        Overrides, ambiguous references and changed global bindings fail
        before any resource is written. ``resources`` bounds the reference
        search and must include all source and destination resources.
        """
        destination_module = project.get_pymodule(destination_resource)
        binding = evaluate.eval_location(destination_module, destination_offset)
        destination = None if binding is None else binding.get_object()
        if not isinstance(destination, pyobjects.PyClass):
            raise exceptions.RefactoringError(
                "Move destination must be a source class."
            )
        if destination.get_module() is not destination_module:
            raise exceptions.RefactoringError(
                "Destination must identify its declaration."
            )
        selected = list(project.get_python_files() if resources is None else resources)
        selected = list(dict.fromkeys(selected))
        methods = {}
        regions = {}
        for resource, offset, new_name in moves:
            module = project.get_pymodule(resource)
            name = evaluate.eval_location(module, offset)
            function = None if name is None else name.get_object()
            if (
                not isinstance(function, pyobjects.PyFunction)
                or not isinstance(function.parent, pyobjects.PyClass)
                or function.get_module() is not module
            ):
                raise exceptions.RefactoringError(
                    "Move source must identify a method declaration."
                )
            if (
                not new_name.isidentifier()
                or keyword.iskeyword(new_name)
                or new_name in destination.get_attributes()
                or new_name in (item[1] for item in methods.values())
                or function in methods
            ):
                raise exceptions.RefactoringError(
                    "Duplicate or occupied method destination."
                )
            cls._check_class_method(function)
            if (
                cls._class_move_effective_name(function.get_name(), function.parent)
                != function.get_name()
                or cls._class_move_effective_name(new_name, destination) != new_name
            ):
                raise exceptions.RefactoringError(
                    "Private method declaration has references outside the native name search."
                )
            cls._check_class_move_lexical(function, destination)
            cls._check_class_move_slots(function.parent, (function,))
            methods[function] = (name, new_name)
            regions[function] = cls._class_method_region(function)
        if not methods:
            raise exceptions.RefactoringError("No methods selected for class transfer.")
        if destination_resource not in selected or any(
            function.get_module().get_resource() not in selected for function in methods
        ):
            raise exceptions.RefactoringError(
                "Move resources exclude a source or destination."
            )
        destination_path = []
        scope = destination.get_scope()
        while scope.get_kind() != "Module":
            if scope.get_kind() != "Class":
                raise exceptions.RefactoringError(
                    "Destination class must have a module-owned path."
                )
            destination_path.insert(0, scope.pyobject.get_name())
            scope = scope.parent
        target = ".".join(destination_path)
        placeholder = "__rope_class_move_destination__"
        sources = {resource: resource.read() for resource in selected}
        if any(placeholder in source for source in sources.values()):
            raise exceptions.RefactoringError("Move placeholder conflicts with source.")
        edits = {resource: {} for resource in selected}
        edges = {function: [] for function in methods}
        for function, (name, new_name) in methods.items():
            occurrences.check_opaque_references(
                project, name, selected, name=function.get_name()
            )
            finder = occurrences.create_finder(
                project,
                function.get_name(),
                name,
                imports=False,
                in_hierarchy=True,
                unsure=lambda occurrence: True,
            )
            for resource in selected:
                module = project.get_pymodule(resource)
                for occurrence in finder.find_occurrences(pymodule=module):
                    actual = occurrence.get_pyname()
                    if (
                        occurrences.unsure_pyname(name, unbound=False)
                        or occurrences.unsure_pyname(actual, unbound=False)
                        or not occurrences.same_pyname(name, actual)
                    ):
                        raise exceptions.RefactoringError(
                            f"Unproven reference or override: {resource.path}:{occurrence.lineno}"
                        )
                    if occurrence.is_defined():
                        continue
                    if occurrence.is_written() or occurrence.is_in_import_statement():
                        raise exceptions.RefactoringError(
                            "Cannot transfer a written method reference."
                        )
                    start, end = occurrence.get_primary_range()
                    enclosing = next(
                        (
                            owner
                            for owner, (first, last) in regions.items()
                            if owner.get_module() is module and first <= start < last
                        ),
                        None,
                    )
                    words = worder.Worder(module.source_code)
                    prefix, _, _ = words.get_splitted_primary_before(end)
                    environment = (
                        module.get_source_binding_index().context_at(start).environment
                    )
                    receiver = (
                        evaluate.eval_str(environment, prefix) if prefix else None
                    )
                    if prefix and occurrences.unsure_pyname(receiver, unbound=False):
                        raise exceptions.RefactoringError(
                            "Method receiver has no resolved native binding."
                        )
                    parameter = None
                    if enclosing is not None:
                        edges[enclosing].append((start, end, function))
                        if enclosing.get_kind() == "classmethod":
                            parameter = enclosing.get_scope().get_names()[
                                enclosing.get_param_names()[0]
                            ]
                    if (
                        parameter is not None
                        and not occurrences.unsure_pyname(parameter, unbound=False)
                        and not occurrences.unsure_pyname(receiver, unbound=False)
                        and occurrences.same_pyname(parameter, receiver)
                    ):
                        start, end = occurrence.get_word_range()
                        replacement = new_name
                    else:
                        if prefix and not all(
                            part.isidentifier() for part in prefix.split(".")
                        ):
                            raise exceptions.RefactoringError(
                                "Effectful method receiver cannot be removed."
                            )
                        if "." in prefix:
                            if occurrences.unsure_pyname(
                                receiver, unbound=False
                            ) or not isinstance(
                                receiver.get_object(),
                                (pyobjects.AbstractClass, pyobjects.AbstractModule),
                            ):
                                raise exceptions.RefactoringError(
                                    "Unproven method receiver evaluation."
                                )
                        replacement = f"{placeholder}.{new_name}"
                    edits[resource][start, end] = replacement
        imports = []
        moved_sources = []
        for function, (_, new_name) in methods.items():
            module = function.get_module()
            resource = module.get_resource()
            start, end = regions[function]
            cls._check_class_receiver(function, methods)
            collector = codeanalyze.ChangeCollector(sources[resource][start:end])
            words = worder.Worder(module.source_code)
            first = words.find_function_offset(
                module.lines.get_line_start(function.get_ast().lineno)
            )
            last = first + len(function.get_name())
            collector.add_change(first - start, last - start, new_name)
            for (first, last), replacement in edits[resource].items():
                if start <= first and last <= end:
                    collector.add_change(first - start, last - start, replacement)
            moving = sourceutils.fix_indentation(
                collector.get_changed() or sources[resource][start:end], 0
            )
            moving, used_imports = moving_code_with_imports(project, resource, moving)
            moved_sources.append(moving.rstrip())
            imports.extend(used_imports)
        planned = {}
        for resource, source in sources.items():
            collector = codeanalyze.ChangeCollector(source)
            removed = [
                function
                for function in methods
                if function.get_module().get_resource() == resource
            ]
            for function in removed:
                first, last = regions[function]
                parent = function.parent
                siblings = [item for item in methods if item.parent is parent]
                empty = all(
                    node in [item.get_ast() for item in siblings]
                    for node in parent.get_ast().body
                )
                replacement = ""
                if empty and function is siblings[0]:
                    replacement = sourceutils.fix_indentation(
                        "pass\n",
                        sourceutils.get_indents(
                            function.get_module().lines, function.get_ast().lineno
                        ),
                    )
                collector.add_change(first, last, replacement)
            for (first, last), replacement in edits[resource].items():
                if not any(
                    regions[item][0] <= first < regions[item][1] for item in removed
                ):
                    collector.add_change(first, last, replacement)
            result = collector.get_changed()
            if result is not None:
                planned[resource] = result
        module = libutils.get_string_module(
            project,
            planned.get(destination_resource, sources[destination_resource]),
            destination_resource,
        )
        scope = module.get_scope()
        for part in destination_path:
            matches = [
                child
                for child in scope.get_scopes()
                if child.get_kind() == "Class" and child.pyobject.get_name() == part
            ]
            if len(matches) != 1:
                raise exceptions.RefactoringError(
                    "Destination class is no longer unique."
                )
            scope = matches[0]
        result = sourceutils.add_methods(module, scope, moved_sources)
        module = libutils.get_string_module(project, result, destination_resource)
        cls._check_class_move_imports(project, module, imports)
        result = _add_imports_to_module(
            importutils.ImportTools(project), module, imports
        )
        planned[destination_resource] = result
        for resource, source in list(planned.items()):
            if placeholder in source:
                module = libutils.get_string_module(project, source, resource)
                if resource == destination_resource:
                    imported = target
                else:
                    source, imported = importutils.add_import(
                        project,
                        module,
                        libutils.modname(destination_resource),
                        destination_path[0],
                    )
                    imported = ".".join((imported, *destination_path[1:]))
                imported_module = libutils.get_string_module(project, source, resource)
                expected = destination
                if resource == destination_resource:
                    expected = imported_module
                    for part in destination_path:
                        expected = expected[part].get_object()
                index = imported_module.get_source_binding_index()
                finder = occurrences.Finder(project, placeholder)
                for occurrence in finder.find_occurrences(pymodule=imported_module):
                    environment = index.context_at(occurrence.offset).environment
                    imported_name = evaluate.eval_str(environment, imported)
                    if (
                        occurrences.unsure_pyname(imported_name, unbound=False)
                        or imported_name.get_object() is not expected
                    ):
                        raise exceptions.RefactoringError(
                            "Destination import conflicts with a consumer binding."
                        )
                planned[resource] = source.replace(placeholder, imported)
        cls._check_class_move_globals(
            project,
            destination_resource,
            planned[destination_resource],
            destination_path,
            methods,
            edges,
        )
        changes = ChangeSet("Moving methods to class <{}>".format(target))
        jobs = task_handle.create_jobset("Collecting Changes", len(planned))
        for resource, source in planned.items():
            jobs.started_job(resource.path)
            if source != sources[resource]:
                changes.add_change(ChangeContents(resource, source))
            jobs.finished_job()
        return changes

    @staticmethod
    def _check_class_method(function):
        kind = function.get_kind()
        if kind not in ("staticmethod", "classmethod") or len(function.decorators) != 1:
            raise exceptions.RefactoringError(
                "Only native static and class methods can be transferred."
            )
        decorator = evaluate.eval_node(
            function.parent.get_scope(), function.decorators[0]
        )
        expected = staticmethod if kind == "staticmethod" else classmethod
        if (
            decorator is None
            or getattr(decorator.get_object(), "builtin", None) is not expected
        ):
            raise exceptions.RefactoringError(
                "Method decorator has no native identity."
            )
        if kind == "classmethod" and not function.get_param_names():
            raise exceptions.RefactoringError(
                "Class method has no class receiver parameter."
            )

    @staticmethod
    def _class_method_region(function):
        module = function.get_module()
        return (
            module.lines.get_line_start(
                get_first_decorator_or_function_start_line(function.get_ast())
            ),
            min(
                module.lines.get_line_end(function.get_scope().get_end()) + 1,
                len(module.source_code),
            ),
        )

    @staticmethod
    def _check_class_move_imports(project, module, imports):
        context = importinfo.ImportContext(project, module.get_resource().parent)
        for info in imports:
            imported_module = libutils.get_string_module(
                project, info.get_import_statement() + "\n", module.get_resource()
            )
            for name in info.get_imported_names(context):
                existing = module.get_attributes().get(name)
                incoming = imported_module.get_attributes().get(name)
                if occurrences.unsure_pyname(incoming, unbound=False) or (
                    existing is not None
                    and (
                        occurrences.unsure_pyname(existing, unbound=False)
                        or not occurrences.same_pyname(existing, incoming)
                    )
                ):
                    raise exceptions.RefactoringError(
                        f"Moved import conflicts with destination: {name}"
                    )

    @staticmethod
    def _check_class_receiver(function, methods):
        nodes = tuple(ast.walk(function.get_ast()))
        index = function.get_module().get_source_binding_index()
        parameter = None
        if function.get_kind() == "classmethod":
            parameter = function.get_scope().get_names()[function.get_param_names()[0]]
            if occurrences.unsure_pyname(parameter, unbound=False):
                raise exceptions.RefactoringError(
                    "Class receiver has no resolved native binding."
                )
        allowed = set()
        for node in nodes:
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                context = index.context_for_node(node.value)
                name = evaluate.eval_node(context.environment, node.value)
                if (
                    parameter is not None
                    and not occurrences.unsure_pyname(parameter, unbound=False)
                    and not occurrences.unsure_pyname(name, unbound=False)
                    and occurrences.same_pyname(parameter, name)
                ):
                    value = evaluate.eval_node(
                        index.context_for_node(node).environment, node
                    )
                    if (
                        not occurrences.unsure_pyname(value, unbound=False)
                        and getattr(value, "source_pyname", value).get_object()
                        in methods
                    ):
                        allowed.add(id(node.value))
        for node in nodes:
            if not isinstance(node, ast.Name):
                continue
            if node.id in ("super", "__class__"):
                raise exceptions.RefactoringError(
                    "Method depends on its original class closure."
                )
            if parameter is not None:
                context = index.context_for_node(node)
                name = evaluate.eval_node(context.environment, node)
                if (
                    not occurrences.unsure_pyname(parameter, unbound=False)
                    and not occurrences.unsure_pyname(name, unbound=False)
                    and occurrences.same_pyname(parameter, name)
                    and id(node) not in allowed
                ):
                    raise exceptions.RefactoringError(
                        "Method retains a dependency on its original class."
                    )

    @classmethod
    def _check_class_move_globals(cls, project, resource, source, path, methods, edges):
        module = libutils.get_string_module(project, source, resource)
        owner = module
        for part in path:
            owner = owner[part].get_object()
        candidates = {
            original: owner[name].get_object()
            for original, (_, name) in methods.items()
        }
        cls._check_class_move_slots(owner, tuple(candidates.values()))
        for original, (_, new_name) in methods.items():
            moved = candidates[original]
            cls._check_class_method(moved)
            moved_start, moved_end = cls._class_method_region(moved)
            observed_edges = []
            for target, (_, target_name) in methods.items():
                expected = owner[target_name]
                finder = occurrences.create_finder(
                    project,
                    target_name,
                    expected,
                    imports=False,
                    unsure=lambda occurrence: True,
                )
                for occurrence in finder.find_occurrences(pymodule=module):
                    first, last = occurrence.get_primary_range()
                    if occurrence.is_defined() or not moved_start <= first < moved_end:
                        continue
                    actual = occurrence.get_pyname()
                    if (
                        occurrences.unsure_pyname(expected, unbound=False)
                        or occurrences.unsure_pyname(actual, unbound=False)
                        or not occurrences.same_pyname(expected, actual)
                    ):
                        raise exceptions.RefactoringError(
                            "Transferred callee has no proven native identity."
                        )
                    observed_edges.append((first, last, target))
            original_edges = sorted(edges[original], key=lambda edge: edge[:2])
            observed_edges.sort(key=lambda edge: edge[:2])
            if [item[2] for item in original_edges] != [
                item[2] for item in observed_edges
            ]:
                raise exceptions.RefactoringError(
                    "Transferred method changes its native callee edges."
                )
            before_positions = codeanalyze.ASTLinesAdapter(
                original.get_module().source_code
            )
            after_positions = codeanalyze.ASTLinesAdapter(module.source_code)
            original_names = [
                node
                for node in ast.walk(original.get_ast())
                if isinstance(node, ast.Name)
                and not any(
                    first <= before_positions[node][0] < last
                    for first, last, _ in original_edges
                )
            ]
            moved_names = [
                node
                for node in ast.walk(moved.get_ast())
                if isinstance(node, ast.Name)
                and not any(
                    first <= after_positions[node][0] < last
                    for first, last, _ in observed_edges
                )
            ]
            if len(original_names) != len(moved_names):
                raise exceptions.RefactoringError(
                    "Transferred method changed its native name structure."
                )
            before = original.get_module().get_source_binding_index()
            after = module.get_source_binding_index()
            for old, new in zip(original_names, moved_names):
                context = before.context_for_node(old)
                old_name = evaluate.eval_node(context.environment, old)
                if old_name is None or not getattr(
                    old_name, "is_refactoring_identity_safe", True
                ):
                    raise exceptions.RefactoringError(
                        f"Unproven method binding: {old.id}"
                    )
                if cls._class_move_local_binding(
                    context, old.id, old_name, original.get_scope()
                ):
                    continue
                if occurrences.unsure_pyname(old_name, unbound=False):
                    raise exceptions.RefactoringError(
                        f"Unproven method dependency: {old.id}"
                    )
                new_name = evaluate.eval_node(
                    after.context_for_node(new).environment, new
                )
                if occurrences.unsure_pyname(
                    new_name, unbound=False
                ) or not occurrences.same_pyname(old_name, new_name):
                    raise exceptions.RefactoringError(
                        f"Transferred method changes binding: {old.id}"
                    )

    @staticmethod
    def _class_move_local_binding(context, name, binding, root_scope):
        if isinstance(binding, (pynames.ImportedName, pynames.ImportedModule)):
            return False
        scope = context.structural_scope
        scopes = []
        while scope is not None and scope.get_kind() != "Module":
            scopes.append(scope)
            if scope is root_scope:
                break
            scope = scope.parent
        if not scopes or scopes[-1] is not root_scope:
            return False
        for scope in (context.environment, *scopes):
            if (
                scope.get_kind() != "Class"
                and scope.get_defined_names().get(name) is binding
            ):
                return True
        return False

    @staticmethod
    def _check_class_move_slots(owner, functions):
        # Follow the same selections as evaluate._Call and _Subscript.
        if "__new__" in owner:
            name = owner["__new__"]
            if occurrences.unsure_pyname(name, unbound=False):
                raise exceptions.RefactoringError("Construction slot is unresolved.")
            if name.get_object() in functions:
                raise exceptions.RefactoringError(
                    "Implicit construction references are not transferable."
                )
        metaclass = owner.get_metaclass()
        if not isinstance(metaclass, pyobjects.AbstractClass):
            raise exceptions.RefactoringError("Subscription metaclass is unresolved.")
        if "__getitem__" in metaclass:
            name = metaclass["__getitem__"]
        elif "__class_getitem__" in owner:
            name = owner["__class_getitem__"]
        else:
            return
        if occurrences.unsure_pyname(name, unbound=False):
            raise exceptions.RefactoringError("Subscription slot is unresolved.")
        called, _ = evaluate.get_callable(name)
        if called in functions:
            raise exceptions.RefactoringError(
                "Implicit subscription references are not transferable."
            )

    @staticmethod
    def _class_move_effective_name(name, owner):
        prefix = owner.get_name().lstrip("_")
        if (
            name.startswith("__")
            and not name.endswith("__")
            and "." not in name
            and prefix
        ):
            return f"_{prefix}{name}"
        return name

    @classmethod
    def _check_class_move_lexical(cls, function, destination):
        if function.parent.get_name().lstrip("_") == destination.get_name().lstrip("_"):
            return
        module = function.get_module()
        index = module.get_source_binding_index()
        positions = codeanalyze.ASTLinesAdapter(module.source_code)
        imports = {layout.region: layout for layout in index.import_layouts()}

        def check_name(name):
            old = cls._class_move_effective_name(name, function.parent)
            new = cls._class_move_effective_name(name, destination)
            if old != new:
                raise exceptions.RefactoringError(
                    f"Transferred method changes private identifier: {name} ({old} -> {new})"
                )

        pending = [function.get_scope()]
        visited = set()
        while pending:
            scope = pending.pop()
            if id(scope) in visited or scope.get_kind() == "Class":
                continue
            visited.add(id(scope))
            for name in scope.get_defined_names():
                check_name(name)
            pending.extend(scope.get_scopes())
            pending.extend(scope.get_binding_scopes())

        def check(node):
            if isinstance(node, ast.Name):
                check_name(node.id)
            elif isinstance(node, ast.Attribute):
                check_name(node.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                layout = imports[positions[node]]
                for name, alias, _region in layout.aliases:
                    check_name(name)
                    if alias is not None:
                        check_name(alias)
            if isinstance(node, ast.ClassDef):
                # Its declaration/header belongs to the enclosing context;
                # its body keeps the nested class's unchanged private prefix.
                for header in (*node.decorator_list, *node.bases, *node.keywords):
                    ast.call_for_nodes(header, check)
                return True
            return False

        ast.call_for_nodes(function.get_ast(), check)

    def get_changes(
        self,
        dest_attr: str,
        new_name: Optional[str] = None,
        resources: Optional[List[resources.File]] = None,
        task_handle=taskhandle.DEFAULT_TASK_HANDLE,  # FIXME: this is unused
    ):
        """Return the changes needed for this refactoring

        Parameters:

        - `dest_attr`: the name of the destination attribute
        - `new_name`: the name of the new method; if `None` uses
          the old name
        - `resources` can be a list of `rope.base.resources.File` to
          apply this refactoring on.  If `None`, the restructuring
          will be applied to all python files.

        """
        functionutils._check_signature_parameters(self.pyfunction)
        changes = ChangeSet(f"Moving method <{self.method_name}>")
        if resources is None:
            resources = self.project.get_python_files()
        if new_name is None:
            new_name = self.get_method_name()
        resource1, start1, end1, new_content1 = self._get_changes_made_by_old_class(
            dest_attr, new_name
        )
        collector1 = codeanalyze.ChangeCollector(resource1.read())
        collector1.add_change(start1, end1, new_content1)

        resource2, start2, end2, new_content2 = self._get_changes_made_by_new_class(
            dest_attr, new_name
        )
        if resource1 == resource2:
            collector1.add_change(start2, end2, new_content2)
        else:
            collector2 = codeanalyze.ChangeCollector(resource2.read())
            collector2.add_change(start2, end2, new_content2)
            result = collector2.get_changed()
            import_tools = importutils.ImportTools(self.project)
            new_imports = self._get_used_imports(import_tools)
            if new_imports:
                goal_pymodule = libutils.get_string_module(
                    self.project, result, resource2
                )
                result = _add_imports_to_module(
                    import_tools, goal_pymodule, new_imports
                )
            if resource2 in resources:
                changes.add_change(ChangeContents(resource2, result))

        if resource1 in resources:
            changes.add_change(ChangeContents(resource1, collector1.get_changed()))
        return changes

    def get_method_name(self):
        return self.method_name

    def _get_used_imports(self, import_tools):
        return importutils.get_imports(self.project, self.pyfunction)

    def _get_changes_made_by_old_class(self, dest_attr, new_name):
        pymodule = self.pyfunction.get_module()
        indents = self._get_scope_indents(self.pyfunction)
        body = "return self.{}.{}({})\n".format(
            dest_attr,
            new_name,
            self._get_passed_arguments_string(),
        )
        region = sourceutils.get_body_region(self.pyfunction)
        return (
            pymodule.get_resource(),
            region[0],
            region[1],
            sourceutils.fix_indentation(body, indents),
        )

    def _get_scope_indents(self, pyobject):
        pymodule = pyobject.get_module()
        return sourceutils.get_indents(
            pymodule.lines, pyobject.get_scope().get_start()
        ) + sourceutils.get_indent(self.project)

    def _get_changes_made_by_new_class(self, dest_attr, new_name):
        old_pyclass = self.pyfunction.parent
        if dest_attr not in old_pyclass:
            raise exceptions.RefactoringError(
                "Destination attribute <%s> not found" % dest_attr
            )
        pyclass = old_pyclass[dest_attr].get_object().get_type()
        if not isinstance(pyclass, pyobjects.PyClass):
            raise exceptions.RefactoringError(
                "Unknown class type for attribute <%s>" % dest_attr
            )
        pymodule = pyclass.get_module()
        resource = pyclass.get_module().get_resource()
        start, end = sourceutils.get_body_region(pyclass)
        pre_blanks = "\n"
        if pymodule.source_code[start:end].strip() != "pass":
            pre_blanks = "\n\n"
            start = end
        indents = self._get_scope_indents(pyclass)
        body = pre_blanks + sourceutils.fix_indentation(
            self.get_new_method(new_name), indents
        )
        return resource, start, end, body

    def get_new_method(self, name):
        functionutils._check_signature_parameters(self.pyfunction)
        return "{}\n{}".format(
            self._get_new_header(name),
            sourceutils.fix_indentation(
                self._get_body(), sourceutils.get_indent(self.project)
            ),
        )

    def _get_unchanged_body(self):
        return sourceutils.get_body(self.pyfunction)

    def _get_body(self, host="host"):
        self_name = self._get_self_name()
        body = self_name + " = None\n" + self._get_unchanged_body()
        pymodule = libutils.get_string_module(self.project, body)
        finder = occurrences.create_finder(self.project, self_name, pymodule[self_name])
        result = rename.rename_in_module(finder, host, pymodule=pymodule)
        if result is None:
            result = body
        return result[result.index("\n") + 1 :]

    def _get_self_name(self):
        return self.pyfunction.get_param_names()[0]

    def _get_new_header(self, name):
        header = "def %s(self" % name
        if self._is_host_used():
            header += ", host"
        definition_info = functionutils.DefinitionInfo.read(self.pyfunction)
        others = definition_info.arguments_to_string(1)
        if others:
            header += ", " + others
        return header + "):"

    def _get_passed_arguments_string(self):
        result = ""
        if self._is_host_used():
            result = "self"
        definition_info = functionutils.DefinitionInfo.read(self.pyfunction)
        others = definition_info.arguments_to_string(1)
        if others:
            if result:
                result += ", "
            result += others
        return result

    def _is_host_used(self):
        return self._get_body("__old_self") != self._get_unchanged_body()


class MoveGlobal:
    """For moving global function and classes"""

    project: project.Project

    def __init__(self, project, resource, offset):
        self.project = project
        this_pymodule = self.project.get_pymodule(resource)
        self.old_pyname = evaluate.eval_location(this_pymodule, offset)
        if self.old_pyname is None:
            raise exceptions.RefactoringError(
                "Move refactoring should be performed on a class/function/variable."
            )
        if self._is_variable(self.old_pyname):
            self.old_name = worder.get_name_at(resource, offset)
            pymodule = this_pymodule
        else:
            self.old_name = self.old_pyname.get_object().get_name()
            pymodule = self.old_pyname.get_object().get_module()
        self._check_exceptional_conditions()
        self.source = pymodule.get_resource()
        self.tools = _MoveTools(
            self.project, self.source, self.old_pyname, self.old_name
        )
        self.import_tools = self.tools.import_tools

    def _import_filter(self, stmt):
        module_name = libutils.modname(self.source)

        if isinstance(stmt.import_info, importutils.NormalImport):
            # Affect any statement that imports the source module
            return any(
                module_name == name
                for name, alias in stmt.import_info.names_and_aliases
            )
        elif isinstance(stmt.import_info, importutils.FromImport):
            # Affect statements importing from the source package
            if "." in module_name:
                package_name, basename = module_name.rsplit(".", 1)
                if stmt.import_info.module_name == package_name and any(
                    basename == name
                    for name, alias in stmt.import_info.names_and_aliases
                ):
                    return True
            return stmt.import_info.module_name == module_name
        return False

    def _check_exceptional_conditions(self):
        if self._is_variable(self.old_pyname):
            pymodule = self.old_pyname.get_definition_location()[0]
            try:
                pymodule.get_scope().get_name(self.old_name)
            except exceptions.NameNotFoundError:
                self._raise_refactoring_error()
        elif not (
            isinstance(self.old_pyname.get_object(), pyobjects.PyDefinedObject)
            and self._is_global(self.old_pyname.get_object())
        ):
            self._raise_refactoring_error()

    def _raise_refactoring_error(self):
        raise exceptions.RefactoringError(
            "Move refactoring should be performed on a global class, function "
            "or variable."
        )

    def _is_global(self, pyobject):
        return pyobject.get_scope().parent == pyobject.get_module().get_scope()

    def _is_variable(self, pyname):
        return isinstance(pyname, pynames.AssignedName)

    def get_changes(
        self,
        dest: Optional[Union[str, resources.Resource]],
        resources: Optional[List[resources.File]] = None,
        task_handle=taskhandle.DEFAULT_TASK_HANDLE,
    ):
        """Return the changes needed for this refactoring

        Parameters:

        - `dest`: the Resource or dotted moddule name of the move destination
        - `resources` can be a list of `rope.base.resources.File` to
          apply this refactoring on.  If `None`, the restructuring
          will be applied to all python files.

        """
        # To do (in another PR): Create a new var: dest_resource: resource.Reource
        # Doing so should remove the type:ignore below.
        if isinstance(dest, str):
            dest = self.project.find_module(dest)
        if resources is None:
            resources = self.project.get_python_files()
        # The previous guards protect against this mypy complaint:
        # "Resource" has no attribute "has_child"
        if dest is None or not dest.exists():
            raise exceptions.RefactoringError("Move destination does not exist.")
        if dest.is_folder() and dest.has_child("__init__.py"):  # type: ignore[attr-defined]
            dest = dest.get_child("__init__.py")  # type: ignore
        # The previous guards protect against this mypy complaint:
        # Item "None" of "Union[str, Resource, None]" has no attribute "is_folder"
        if dest.is_folder():  # type: ignore
            raise exceptions.RefactoringError(
                "Move destination for non-modules should not be folders."
            )
        if self.source == dest:
            raise exceptions.RefactoringError(
                "Moving global elements to the same module."
            )
        return self._calculate_changes(dest, resources, task_handle)

    def _calculate_changes(self, dest, resources, task_handle):
        changes = ChangeSet("Moving global <%s>" % self.old_name)
        job_set = task_handle.create_jobset("Collecting Changes", len(resources))
        for file_ in resources:
            job_set.started_job(file_.path)
            if file_ == self.source:
                changes.add_change(self._source_module_changes(dest))
            elif file_ == dest:
                changes.add_change(self._dest_module_changes(dest))
            elif self.tools.occurs_in_module(resource=file_):
                pymodule = self.project.get_pymodule(file_)
                # Changing occurrences
                placeholder = "__rope_renaming_%s_" % self.old_name
                source = self.tools.rename_in_module(placeholder, resource=file_)
                should_import = source is not None
                # Removing out of date imports
                pymodule = self.tools.new_pymodule(pymodule, source)
                source = self.import_tools.organize_imports(
                    pymodule, sort=False, import_filter=self._import_filter
                )
                # Adding new import
                if should_import:
                    pymodule = self.tools.new_pymodule(pymodule, source)
                    source, imported = importutils.add_import(
                        self.project, pymodule, self._new_modname(dest), self.old_name
                    )
                    source = source.replace(placeholder, imported)
                source = self.tools.new_source(pymodule, source)
                if source != file_.read():
                    changes.add_change(ChangeContents(file_, source))
            job_set.finished_job()
        return changes

    def _source_module_changes(self, dest):
        placeholder = "__rope_moving_%s_" % self.old_name
        handle = _ChangeMoveOccurrencesHandle(placeholder)
        occurrence_finder = occurrences.create_finder(
            self.project, self.old_name, self.old_pyname
        )
        start, end = self._get_moving_region()
        renamer = ModuleSkipRenamer(occurrence_finder, self.source, handle, start, end)
        source = renamer.get_changed_module()
        pymodule = libutils.get_string_module(self.project, source, self.source)
        source = self.import_tools.organize_imports(pymodule, sort=False)
        if handle.occurred:
            pymodule = libutils.get_string_module(self.project, source, self.source)
            # Adding new import
            source, imported = importutils.add_import(
                self.project, pymodule, self._new_modname(dest), self.old_name
            )
            source = source.replace(placeholder, imported)
        return ChangeContents(self.source, source)

    def _new_modname(self, dest):
        return libutils.modname(dest)

    def _dest_module_changes(self, dest):
        # Changing occurrences
        pymodule = self.project.get_pymodule(dest)
        source = self.tools.rename_in_module(self.old_name, pymodule)
        pymodule = self.tools.new_pymodule(pymodule, source)

        moving, imports = self._get_moving_element_with_imports()
        pymodule, has_changed = self._add_imports2(pymodule, imports)

        module_with_imports = self.import_tools.module_imports(pymodule)
        source = pymodule.source_code
        lineno = 0
        if module_with_imports.imports:
            lineno = module_with_imports.imports[-1].end_line - 1
        else:
            while lineno < pymodule.lines.length() and pymodule.lines.get_line(
                lineno + 1
            ).lstrip().startswith("#"):
                lineno += 1
        if lineno > 0:
            cut = pymodule.lines.get_line_end(lineno) + 1
            result = source[:cut] + "\n\n" + moving + source[cut:]
        else:
            result = moving + source

        # Organizing imports
        source = result
        pymodule = libutils.get_string_module(self.project, source, dest)
        source = self.import_tools.organize_imports(pymodule, sort=False, unused=False)
        # Remove unused imports of the old module
        pymodule = libutils.get_string_module(self.project, source, dest)
        source = self.import_tools.organize_imports(
            pymodule,
            sort=False,
            selfs=False,
            unused=True,
            import_filter=self._import_filter,
        )
        return ChangeContents(dest, source)

    def _get_moving_element_with_imports(self):
        return moving_code_with_imports(
            self.project, self.source, self._get_moving_element()
        )

    def _get_module_with_imports(self, source_code, resource):
        pymodule = libutils.get_string_module(self.project, source_code, resource)
        return self.import_tools.module_imports(pymodule)

    def _get_moving_element(self):
        start, end = self._get_moving_region()
        moving = self.source.read()[start:end]
        return moving.rstrip() + "\n"

    def _get_moving_region(self):
        pymodule = self.project.get_pymodule(self.source)
        lines = pymodule.lines
        if self._is_variable(self.old_pyname):
            logical_lines = pymodule.logical_lines
            lineno = logical_lines.logical_line_in(
                self.old_pyname.get_definition_location()[1]
            )[0]
            start = lines.get_line_start(lineno)
            end_line = logical_lines.logical_line_in(lineno)[1]
        else:
            node = self.old_pyname.get_object().ast_node
            start = lines.get_line_start(
                get_first_decorator_or_function_start_line(node)
            )
            end_line = self.old_pyname.get_object().get_scope().get_end()

        # Include comment lines before the definition
        start_line = lines.get_line_number(start)
        while start_line > 1 and lines.get_line(start_line - 1).startswith("#"):
            start_line -= 1
        start = lines.get_line_start(start_line)

        while end_line < lines.length() and lines.get_line(end_line + 1).strip() == "":
            end_line += 1
        end = min(lines.get_line_end(end_line) + 1, len(pymodule.source_code))
        return start, end

    def _add_imports2(self, pymodule, new_imports):
        source = self.tools.add_imports(pymodule, new_imports)
        if source is None:
            return pymodule, False
        else:
            resource = pymodule.get_resource()
            pymodule = libutils.get_string_module(self.project, source, resource)
            return pymodule, True


class MoveModule:
    """For moving modules and packages"""

    def __init__(self, project, resource):
        self.project = project
        if not resource.is_folder() and resource.name == "__init__.py":
            resource = resource.parent
        if resource.is_folder() and not resource.has_child("__init__.py"):
            raise exceptions.RefactoringError("Cannot move non-package folder.")
        dummy_pymodule = libutils.get_string_module(self.project, "")
        self.old_pyname = pynames.ImportedModule(dummy_pymodule, resource=resource)
        self.source = self.old_pyname.get_object().get_resource()
        if self.source.is_folder():
            self.old_name = self.source.name
        else:
            self.old_name = self.source.name[:-3]
        self.tools = _MoveTools(
            self.project, self.source, self.old_pyname, self.old_name
        )
        self.import_tools = self.tools.import_tools

    def get_changes(
        self,
        dest: resources.Resource,
        resources: Optional[List[resources.File]] = None,
        task_handle=taskhandle.DEFAULT_TASK_HANDLE,
    ):
        if resources is None:
            resources = self.project.get_python_files()
        if dest is None or not dest.is_folder():
            raise exceptions.RefactoringError(
                "Move destination for modules should be packages."
            )
        return self._calculate_changes(dest, resources, task_handle)

    def _calculate_changes(self, dest, resources, task_handle):
        changes = ChangeSet("Moving module <%s>" % self.old_name)
        job_set = task_handle.create_jobset("Collecting changes", len(resources))
        for module in resources:
            job_set.started_job(module.path)
            if module == self.source:
                self._change_moving_module(changes, dest)
            else:
                source = self._change_occurrences_in_module(dest, resource=module)
                if source is not None:
                    changes.add_change(ChangeContents(module, source))
            job_set.finished_job()
        if self.project == self.source.project:
            changes.add_change(MoveResource(self.source, dest.path))
        return changes

    def _new_modname(self, dest):
        destname = libutils.modname(dest)
        if destname:
            return destname + "." + self.old_name
        return self.old_name

    def _new_import(self, dest):
        return importutils.NormalImport([(self._new_modname(dest), None)])

    def _change_moving_module(self, changes, dest):
        if not self.source.is_folder():
            pymodule = self.project.get_pymodule(self.source)
            source = self.import_tools.relatives_to_absolutes(pymodule)
            pymodule = self.tools.new_pymodule(pymodule, source)
            source = self._change_occurrences_in_module(dest, pymodule)
            source = self.tools.new_source(pymodule, source)
            if source != self.source.read():
                changes.add_change(ChangeContents(self.source, source))

    def _change_occurrences_in_module(self, dest, pymodule=None, resource=None):
        if not self.tools.occurs_in_module(pymodule=pymodule, resource=resource):
            return
        if pymodule is None:
            pymodule = self.project.get_pymodule(resource)
        new_name = self._new_modname(dest)
        module_imports = importutils.get_module_imports(self.project, pymodule)
        changed = False
        source = None
        if libutils.modname(dest):
            changed = self._change_import_statements(dest, new_name, module_imports)
            if changed:
                source = module_imports.get_changed_source()
                source = self.tools.new_source(pymodule, source)
                pymodule = self.tools.new_pymodule(pymodule, source)

        new_import = self._new_import(dest)
        source = self.tools.rename_in_module(
            new_name,
            imports=True,
            pymodule=pymodule,
            resource=resource if not changed else None,
        )
        should_import = self.tools.occurs_in_module(
            pymodule=pymodule, resource=resource, imports=False
        )
        pymodule = self.tools.new_pymodule(pymodule, source)
        source = self.tools.remove_old_imports(pymodule)
        if should_import:
            pymodule = self.tools.new_pymodule(pymodule, source)
            source = self.tools.add_imports(pymodule, [new_import])
        source = self.tools.new_source(pymodule, source)
        if source is not None and source != pymodule.resource.read():
            return source
        return None

    def _change_import_statements(self, dest, new_name, module_imports):
        moving_module = self.source
        parent_module = moving_module.parent

        changed = False
        for import_stmt in module_imports.imports:
            if not any(
                name_and_alias[0] == self.old_name
                for name_and_alias in import_stmt.import_info.names_and_aliases
            ) and not any(
                name_and_alias[0] == libutils.modname(self.source)
                for name_and_alias in import_stmt.import_info.names_and_aliases
            ):
                continue

            # Case 1: Look for normal imports of the moving module.
            if isinstance(import_stmt.import_info, importutils.NormalImport):
                continue

            # Case 2: The moving module is from-imported.
            changed = (
                self._handle_moving_in_from_import_stmt(
                    dest, import_stmt, module_imports, parent_module
                )
                or changed
            )

            # Case 3: Names are imported from the moving module.
            context = importutils.importinfo.ImportContext(self.project, None)
            if (
                not import_stmt.import_info.is_empty()
                and import_stmt.import_info.get_imported_resource(context)
                == moving_module
            ):
                import_stmt.import_info = importutils.FromImport(
                    new_name,
                    import_stmt.import_info.level,
                    import_stmt.import_info.names_and_aliases,
                )
                changed = True

        return changed

    def _handle_moving_in_from_import_stmt(
        self, dest, import_stmt, module_imports, parent_module
    ):
        changed = False
        context = importutils.importinfo.ImportContext(self.project, None)
        if import_stmt.import_info.get_imported_resource(context) == parent_module:
            imports = import_stmt.import_info.names_and_aliases
            new_imports = []
            for name, alias in imports:
                # The moving module was imported.
                if name == self.old_name:
                    changed = True
                    new_import = importutils.FromImport(
                        libutils.modname(dest), 0, [(self.old_name, alias)]
                    )
                    module_imports.add_import(new_import)
                else:
                    new_imports.append((name, alias))

            # Update the imports if the imported names were changed.
            if new_imports != imports:
                changed = True
                if new_imports:
                    import_stmt.import_info = importutils.FromImport(
                        import_stmt.import_info.module_name,
                        import_stmt.import_info.level,
                        new_imports,
                    )
                else:
                    import_stmt.empty_import()
        return changed


class _ChangeMoveOccurrencesHandle:
    def __init__(self, new_name):
        self.new_name = new_name
        self.occurred = False

    def occurred_inside_skip(self, change_collector, occurrence):
        pass

    def occurred_outside_skip(self, change_collector, occurrence):
        start, end = occurrence.get_primary_range()
        change_collector.add_change(start, end, self.new_name)
        self.occurred = True


class _MoveTools:
    def __init__(self, project, source, pyname, old_name):
        self.project = project
        self.source = source
        self.old_pyname = pyname
        self.old_name = old_name
        self.import_tools = importutils.ImportTools(self.project)

    def remove_old_imports(self, pymodule):
        old_source = pymodule.source_code
        module_with_imports = self.import_tools.module_imports(pymodule)

        class CanSelect:
            changed = False
            old_name = self.old_name
            old_pyname = self.old_pyname

            def __call__(self, name):
                try:
                    if (
                        name == self.old_name
                        and pymodule[name].get_object() == self.old_pyname.get_object()
                    ):
                        self.changed = True
                        return False
                except exceptions.AttributeNotFoundError:
                    pass
                return True

        can_select = CanSelect()
        module_with_imports.filter_names(can_select)
        new_source = module_with_imports.get_changed_source()
        if old_source != new_source:
            return new_source

    def rename_in_module(self, new_name, pymodule=None, imports=False, resource=None):
        occurrence_finder = self._create_finder(imports)
        source = rename.rename_in_module(
            occurrence_finder,
            new_name,
            replace_primary=True,
            pymodule=pymodule,
            resource=resource,
        )
        return source

    def occurs_in_module(self, pymodule=None, resource=None, imports=True):
        finder = self._create_finder(imports)
        occurrences = finder.find_occurrences(pymodule=pymodule, resource=resource)
        sentinel = object()
        return next(occurrences, sentinel) is not sentinel

    def _create_finder(self, imports):
        return occurrences.create_finder(
            self.project,
            self.old_name,
            self.old_pyname,
            imports=imports,
            keywords=False,
        )

    def new_pymodule(self, pymodule, source):
        if source is not None:
            return libutils.get_string_module(
                self.project, source, pymodule.get_resource()
            )
        return pymodule

    def new_source(self, pymodule, source):
        if source is None:
            return pymodule.source_code
        return source

    def add_imports(self, pymodule, new_imports):
        return _add_imports_to_module(self.import_tools, pymodule, new_imports)


def _add_imports_to_module(import_tools, pymodule, new_imports):
    module_with_imports = import_tools.module_imports(pymodule)
    for new_import in new_imports:
        module_with_imports.add_import(new_import)
    return module_with_imports.get_changed_source()


def moving_code_with_imports(project, resource, source):
    import_tools = importutils.ImportTools(project)
    pymodule = libutils.get_string_module(project, source, resource)

    # Strip comment prefix, if any. These need to stay before the moving
    # section, but imports would be added between them.
    lines = codeanalyze.SourceLinesAdapter(source)
    start = 1
    while start < lines.length() and lines.get_line(start).startswith("#"):
        start += 1
    moving_prefix = source[: lines.get_line_start(start)]
    pymodule = libutils.get_string_module(
        project, source[lines.get_line_start(start) :], resource
    )

    origin = project.get_pymodule(resource)

    imports = [stmt.import_info for stmt in import_tools.module_imports(origin).imports]

    back_names = [name for name in origin if name not in pymodule]
    imports.append(import_tools.get_from_import(resource, back_names))

    source = _add_imports_to_module(import_tools, pymodule, imports)
    pymodule = libutils.get_string_module(project, source, resource)

    source = import_tools.relatives_to_absolutes(pymodule)
    pymodule = libutils.get_string_module(project, source, resource)
    source = import_tools.organize_imports(pymodule, selfs=False)
    pymodule = libutils.get_string_module(project, source, resource)

    # extracting imports after changes
    module_imports = import_tools.module_imports(pymodule)
    imports = [import_stmt.import_info for import_stmt in module_imports.imports]
    start = 1
    if module_imports.imports:
        start = module_imports.imports[-1].end_line
    lines = codeanalyze.SourceLinesAdapter(source)
    while start < lines.length() and not lines.get_line(start).strip():
        start += 1

    # Reinsert the prefix which was removed at the beginning
    moving = moving_prefix + source[lines.get_line_start(start) :]
    return moving, imports


class ModuleSkipRenamerHandle:
    def occurred_outside_skip(self, change_collector, occurrence):
        pass

    def occurred_inside_skip(self, change_collector, occurrence):
        pass


class ModuleSkipRenamer:
    """Rename occurrences in a module

    This class can be used when you want to treat a region in a file
    separately from other parts when renaming.

    """

    def __init__(
        self,
        occurrence_finder,
        resource,
        handle=None,
        skip_start=0,
        skip_end=0,
        replacement="",
    ):
        """Constructor

        if replacement is `None` the region is not changed.  Otherwise
        it is replaced with `replacement`.

        """
        self.occurrence_finder = occurrence_finder
        self.resource = resource
        self.skip_start = skip_start
        self.skip_end = skip_end
        self.replacement = replacement
        self.handle = handle
        if self.handle is None:
            self.handle = ModuleSkipRenamerHandle()

    def get_changed_module(self):
        source = self.resource.read()
        change_collector = codeanalyze.ChangeCollector(source)
        if self.replacement is not None:
            change_collector.add_change(
                self.skip_start, self.skip_end, self.replacement
            )
        for occurrence in self.occurrence_finder.find_occurrences(self.resource):
            start, end = occurrence.get_primary_range()
            if self.skip_start <= start < self.skip_end:
                self.handle.occurred_inside_skip(change_collector, occurrence)
            else:
                self.handle.occurred_outside_skip(change_collector, occurrence)
        result = change_collector.get_changed()
        if result is not None and result != source:
            return result
