# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Finds all the code objects defined by a module."""

import gc
import os
import sys
import types

import cdbg_native as native

# Maximum traversal depth when looking for all the code objects referenced by
# a module or another code object.
_MAX_REFERENTS_BFS_DEPTH = 15

# Absolute limit on the amount of objects to scan when looking for all the code
# objects implemented in a module.
_MAX_VISIT_OBJECTS = 100000

# Object types to ignore when looking for the code objects.
_BFS_IGNORE_TYPES = (types.ModuleType, types.NoneType, types.BooleanType,
                     types.IntType, types.LongType, types.FloatType,
                     types.StringType, types.UnicodeType,
                     types.BuiltinFunctionType, types.BuiltinMethodType)


def GetCodeObjectAtLine(module, line):
  """Searches for a code object at the specified line in the specified module.

  Args:
    module: module to explore.
    line: 1-based line number of the statement.

  Returns:
    Code object or None if not found.
  """
  if not hasattr(module, '__file__'):
    return None

  for code_object in _GetModuleCodeObjects(module):
    if native.HasSourceLine(code_object, line):
      return code_object

  return None


def _GetModuleCodeObjects(module):
  """Gets all code objects defined in the specified module.

  There are two BFS traversals involved. One in this function and the other in
  _FindCodeObjectsReferents. Only the BFS in _FindCodeObjectsReferents has
  a depth limit. This function does not. The motivation is that this function
  explores code object of the module and they can have any arbitrary nesting
  level. _FindCodeObjectsReferents, on the other hand, traverses through class
  definitions and random references. It's much more expensive and will likely
  go into unrelated objects.

  There is also a limit on how many total objects are going to be traversed in
  all. This limit makes sure that if something goes wrong, the lookup doesn't
  hang.

  Args:
    module: module to explore.

  Returns:
    Set of code objects defined in module.
  """

  visit_recorder = _VisitRecorder()
  current = [module]
  code_objects = set()
  while current:
    current = _FindCodeObjectsReferents(module, current, visit_recorder)
    code_objects |= current

    # Unfortunately Python code objects don't implement tp_traverse, so this
    # type can't be used with gc.get_referents. The workaround is to get the
    # relevant objects explicitly here.
    current = [code_object.co_consts for code_object in current]

  return code_objects


def _FindCodeObjectsReferents(module, start_objects, visit_recorder):
  """Looks for all the code objects referenced by objects in start_objects.

  The traversal implemented by this function is a shallow one. In other words
  if the reference chain is a -> b -> co1 -> c -> co2, this function will
  return [co1] only.

  The traversal is implemented with BFS. The maximum depth is limited to avoid
  touching all the objects in the process. Each object is only visited once
  using visit_recorder.

  Args:
    module: module in which we are looking for code objects.
    start_objects: initial set of objects for the BFS traversal.
    visit_recorder: instance of _VisitRecorder class to ensure each object is
        visited at most once.

  Returns:
    List of code objects.
  """
  def CheckIgnoreCodeObject(code_object):
    """Checks if the code object can be ignored.

    Code objects that are not implemented in the module, or are from a lambda or
    generator expression can be ignored.

    If the module was precompiled, the code object may point to .py file, while
    the module says that it originated from .pyc file. We just strip extension
    altogether to work around it.

    Args:
      code_object: code object that we want to check against module.

    Returns:
      True if the code object can be ignored, False otherwise.
    """
    if code_object.co_name in ('<lambda>', '<genexpr>'):
      return True

    code_object_file = os.path.splitext(code_object.co_filename)[0]
    module_file = os.path.splitext(module.__file__)[0]

    # The simple case.
    if code_object_file == module_file:
      return False


    return True

  def CheckIgnoreClass(cls):
    """Returns True if the class is definitely not coming from "module"."""
    cls_module = sys.modules.get(cls.__module__)
    if not cls_module:
      return False  # We can't tell for sure, so explore this class.

    return (
        cls_module is not module and
        getattr(cls_module, '__file__', None) != module.__file__)

  code_objects = set()
  current = start_objects
  depth = 0
  while current and depth < _MAX_REFERENTS_BFS_DEPTH:
    referents = gc.get_referents(*current)
    current = []
    for obj in referents:
      if isinstance(obj, _BFS_IGNORE_TYPES) or not visit_recorder.Record(obj):
        continue

      if isinstance(obj, types.CodeType) and CheckIgnoreCodeObject(obj):
        continue

      if isinstance(obj, types.ClassType) and CheckIgnoreClass(obj):
        continue

      if isinstance(obj, types.CodeType):
        code_objects.add(obj)
      else:
        current.append(obj)

    depth += 1

  return code_objects


class _VisitRecorder(object):
  """Helper class to track of already visited objects and implement quota.

  This class keeps a map from integer to object. The key is a unique object
  ID (raw object pointer). The value is the object itself. We need to keep the
  object in the map, so that it doesn't get released during iteration (since
  object ID is only unique as long as the object is alive).
  """

  def __init__(self):
    self._visit_recorder_objects = {}

  def Record(self, obj):
    """Records the object as visited.

    Args:
      obj: visited object.

    Returns:
      True if the object hasn't been previously visited or False if it has
      already been recorded or the quota has been exhausted.
    """
    if len(self._visit_recorder_objects) >= _MAX_VISIT_OBJECTS:
      return False

    obj_id = id(obj)
    if obj_id in self._visit_recorder_objects:
      return False

    self._visit_recorder_objects[obj_id] = obj
    return True
