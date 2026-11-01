"""A module for inferring objects

For more information see the documentation in `rope.base.oi`
package.

"""

import rope.base.builtins  # Use full qualification for clarity.
from rope.base import arguments, ast, evaluate, pynames, pyobjects, utils
from rope.base.oi.type_hinting.factory import get_type_hinting_factory

_ignore_inferred = utils.ignore_exception(pyobjects.IsBeingInferredError)


@_ignore_inferred
def infer_returned_object(pyfunction, args):
    """Infer the `PyObject` this `PyFunction` returns after calling"""
    object_info = pyfunction.pycore.object_info
    result = object_info.get_exact_returned(pyfunction, args)
    if result is not None:
        return result
    result = _infer_returned(pyfunction, args)
    if result is not None:
        if args and pyfunction.get_module().get_resource() is not None:
            params = args.get_arguments(pyfunction.get_param_names(special_args=False))
            object_info.function_called(pyfunction, params, result)
        return result
    result = object_info.get_returned(pyfunction, args)
    if result is not None:
        return result
    hint_return = get_type_hinting_factory(
        pyfunction.pycore.project
    ).make_return_provider()
    type_ = hint_return(pyfunction)
    if type_ is not None:
        return pyobjects.PyObject(type_)


@_ignore_inferred
def infer_parameter_objects(pyfunction):
    """Infer the `PyObject` of parameters of this `PyFunction`"""
    object_info = pyfunction.pycore.object_info
    result = object_info.get_parameter_objects(pyfunction)
    if result is None:
        result = _parameter_objects(pyfunction)
    _handle_first_parameter(pyfunction, result)
    return result


def _handle_first_parameter(pyobject, parameters):
    kind = pyobject.get_kind()
    if not pyobject.get_positional_param_names():
        return
    if not parameters:
        parameters.append(pyobjects.get_unknown())
    if parameters[0] is not None and parameters[0] != pyobjects.get_unknown():
        return
    if kind == "method":
        parameters[0] = pyobjects.PyObject(pyobject.parent)
    if kind == "classmethod":
        parameters[0] = pyobject.parent


def relevant_branch_context(pyname, branch_context):
    """Return the part of `branch_context` that selects `pyname` assignments

    Branches that contain none of the assignments of `pyname` cannot change
    its value and are dropped.

    """
    relevant = set()
    for assignment in pyname.assignments:
        assignment_context = _assignment_branch_context(assignment, pyname.module)
        if assignment_context is not None:
            relevant.update(branch for branch, _ in assignment_context)
    return tuple((branch, arm) for branch, arm in branch_context if branch in relevant)


@_ignore_inferred
def infer_assigned_object(pyname, *, branch_context=(), read_node=None):
    read_point = None
    if pyname.initial_binding is not None:
        source_index = pyname.module._get_source_binding_index()
        read_context = source_index.context_for_node(read_node)
        if (
            read_context is None
            or read_context.structural_scope.pyobject
            is not pyname.initial_binding.pyfunction
        ):
            return pyobjects.get_unknown()
        read_point = source_index._span(read_node)[0]
    context = dict(relevant_branch_context(pyname, branch_context))
    context.update(pyname._inference_assignment_context)
    limit = pyname._inference_assignment_limit
    if limit is None:
        limit = len(pyname.assignments)
    if not limit:
        if any(arm is None for arm in context.values()):
            pyname._inference_assignment_ambiguous = True
            return pyobjects.get_unknown()
        if pyname.initial_binding is not None:
            return pyname.initial_binding.get_object()
        return
    result, ambiguous = _infer_assignment_path(
        pyname,
        limit - 1,
        context,
        pyname._inference_assignment_limit is not None or read_point is not None,
        read_point,
    )
    if ambiguous:
        if pyname._inference_assignment_limit is not None:
            pyname._inference_assignment_ambiguous = True
        return pyobjects.get_unknown()
    if (
        result is not None
        and result is not pyobjects.get_unknown()
        and not (
            isinstance(result, rope.base.builtins.BuiltinUnknown)
            and result.get_name() == "NotImplementedType"
        )
    ):
        return result

    hint_assignment = get_type_hinting_factory(
        pyname.module.pycore.project
    ).make_assignment_provider()
    hinting_result = hint_assignment(pyname)
    if hinting_result is not None:
        return pyobjects.PyObject(hinting_result)
    return result


def _infer_assignment_path(pyname, index, context, recursive, read_point=None):
    if recursive and any(arm is None for arm in context.values()):
        return pyobjects.get_unknown(), True
    while index >= 0:
        assignment = pyname.assignments[index]
        if read_point is not None and assignment.ast_node is None:
            index -= 1
            continue
        branch_context = _assignment_branch_context(assignment, pyname.module)
        if branch_context is None:
            return pyobjects.get_unknown(), True
        if read_point is not None:
            source_index = pyname.module._get_source_binding_index()
            if source_index.context_for_node(assignment.ast_node) is None:
                return pyobjects.get_unknown(), True
            if source_index._span(assignment.ast_node)[1] > read_point:
                index -= 1
                continue
        for branch, arm in branch_context:
            if arm is None:
                continue
            if isinstance(branch.test, ast.Constant):
                selected = bool(branch.test.value)
            else:
                selected = context.get(branch)
            if selected is not None and selected != arm:
                break
            if selected is None:
                matching = dict(context)
                matching[branch] = arm
                other = dict(context)
                other[branch] = not arm
                first, first_ambiguous = _infer_assignment_path(
                    pyname, index, matching, recursive, read_point
                )
                second, second_ambiguous = _infer_assignment_path(
                    pyname, index - 1, other, recursive, read_point
                )
                if not first_ambiguous and not second_ambiguous and first is second:
                    return first, False
                return pyobjects.get_unknown(), True
        else:
            unsupported = {branch: arm for branch, arm in branch_context if arm is None}
            if recursive and unsupported:
                return pyobjects.get_unknown(), True
            previous_limit = pyname._inference_assignment_limit
            previous_context = pyname._inference_assignment_context
            previous_ambiguous = pyname._inference_assignment_ambiguous
            pyname._inference_assignment_limit = index
            pyname._inference_assignment_context = tuple(context.items()) + tuple(
                unsupported.items()
            )
            pyname._inference_assignment_ambiguous = False
            try:
                result = _infer_assignment(
                    assignment,
                    pyname.module,
                    branch_context=tuple(context.items()) + branch_context,
                )
                ambiguous = pyname._inference_assignment_ambiguous
            finally:
                pyname._inference_assignment_limit = previous_limit
                pyname._inference_assignment_context = previous_context
                pyname._inference_assignment_ambiguous = previous_ambiguous
            if ambiguous:
                return pyobjects.get_unknown(), True
            if result is not None:
                return result, False
            if read_point is not None:
                return pyobjects.get_unknown(), True
        index -= 1
    if pyname.initial_binding is not None:
        return pyname.initial_binding.get_object(), False
    return None, False


def _assignment_branch_context(assignment, pymodule):
    node = assignment.type_hint or assignment.ast_node
    context = pymodule._get_source_binding_index().context_for_node(node)
    if context is not None:
        return context.branch_context
    if assignment.branch_context is not None:
        return assignment.branch_context
    # Assignments outside the source binding index (for instance those of an
    # independently parsed expression) carry no branch information; like
    # upstream, they are inferred as unconditional assignments.
    return ()


def get_passed_objects(pyfunction, parameter_index):
    object_info = pyfunction.pycore.object_info
    result = object_info.get_passed_objects(pyfunction, parameter_index)
    if not result:
        statically_inferred = _parameter_objects(pyfunction)
        if len(statically_inferred) > parameter_index:
            result.append(statically_inferred[parameter_index])
    return result


def _infer_returned(pyobject, args):
    if args:
        # HACK: Setting parameter objects manually
        # This is not thread safe and might cause problems if `args`
        # does not come from a good call site
        pyobject.get_scope().invalidate_data()
        pyobject._set_parameter_pyobjects(
            args.get_arguments(pyobject.get_param_names(special_args=False))
        )
    scope = pyobject.get_scope()
    if not scope._get_returned_asts():
        return
    maxtries = 3
    for returned_node in reversed(scope._get_returned_asts()[-maxtries:]):
        try:
            resulting_pyname = evaluate.eval_node(scope, returned_node)
            if resulting_pyname is None:
                continue
            pyobject = resulting_pyname.get_object()
            if pyobject == pyobjects.get_unknown():
                continue
            if not scope._is_generator():
                return pyobject
            else:
                return rope.base.builtins.get_generator(pyobject)
        except pyobjects.IsBeingInferredError:
            pass


def _parameter_objects(pyobject):
    result = []
    params = pyobject.get_param_names(special_args=False)
    hint_param = get_type_hinting_factory(pyobject.pycore.project).make_param_provider()
    for name in params:
        type_ = hint_param(pyobject, name)
        if type_ is not None:
            result.append(pyobjects.PyObject(type_))
        else:
            result.append(pyobjects.get_unknown())
    return result


# handling `rope.base.pynames.AssignmentValue`


@_ignore_inferred
def _infer_assignment(assignment, pymodule, *, branch_context=()):
    assign_node = assignment.type_hint or assignment.ast_node
    if isinstance(assign_node, ast.pattern):
        return pyobjects.get_unknown()
    result = _follow_pyname(assignment, pymodule, branch_context=branch_context)
    if result is None:
        return None
    pyname, pyobject = result
    pyobject = _follow_evaluations(assignment, pyname, pyobject)
    if pyobject is None:
        return None
    return _follow_levels(assignment, pyobject)


def _follow_levels(assignment, pyobject):
    for index in assignment.levels:
        if isinstance(pyobject.get_type(), rope.base.builtins.Tuple):
            holdings = pyobject.get_type().get_holding_objects()
            if holdings:
                pyobject = holdings[min(len(holdings) - 1, index)]
            else:
                pyobject = None
        elif isinstance(pyobject.get_type(), rope.base.builtins.List):
            pyobject = pyobject.get_type().holding
        else:
            pyobject = None
        if pyobject is None:
            break
    return pyobject


@_ignore_inferred
def _follow_pyname(assignment, pymodule, lineno=None, *, branch_context=()):
    assign_node = assignment.type_hint or assignment.ast_node
    if lineno is None:
        lineno = _get_lineno_for_node(assign_node)
    holding_scope = pymodule.get_scope().get_inner_scope_for_line(lineno)
    holding_kind = holding_scope.get_kind()
    if branch_context:
        context = pymodule._get_source_binding_index().context_for_node(assign_node)
        if context is not None:
            holding_scope = context.environment
        holding_scope = evaluate.SourceValueScope(
            holding_scope, holding_scope, {}, branch_context=branch_context
        )
    pyname = evaluate.eval_node(holding_scope, assign_node)
    if pyname is not None:
        result = pyname.get_object()
        if (
            isinstance(result.get_type(), rope.base.builtins.Property)
            and holding_kind == "Class"
        ):
            arg = pynames.UnboundName(pyobjects.PyObject(holding_scope.pyobject))
            return pyname, result.get_type().get_property_object(
                arguments.ObjectArguments([arg])
            )
        return pyname, result


@_ignore_inferred
def _follow_evaluations(assignment, pyname, pyobject):
    new_pyname = pyname
    tokens = assignment.evaluation.split(".")
    for token in tokens:
        call = token.endswith("()")
        if call:
            token = token[:-2]
        if token:
            pyname = new_pyname
            new_pyname = _get_attribute(pyobject, token)
            if new_pyname is not None:
                pyobject = new_pyname.get_object()
        if pyobject is not None and call:
            if isinstance(pyobject, pyobjects.AbstractFunction):
                args = arguments.ObjectArguments([pyname])
                pyobject = pyobject.get_returned_object(args)
            else:
                pyobject = None
        if pyobject is None:
            break
    if pyobject is not None and assignment.assign_type:
        return pyobjects.PyObject(pyobject)
    return pyobject


def _get_lineno_for_node(assign_node):
    if hasattr(assign_node, "lineno") and assign_node.lineno is not None:
        return assign_node.lineno
    return 1


def _get_attribute(pyobject, name):
    if pyobject is not None and name in pyobject:
        return pyobject[name]
