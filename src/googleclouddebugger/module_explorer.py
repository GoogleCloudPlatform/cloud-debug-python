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

import os
import sys
import types

import cdbg_native as native

_METHOD_TYPES = (types.FunctionType, types.MethodType, types.LambdaType)
_CLASS_TYPES = (type, types.ClassType)


def GetCodeObjectAtLine(module, line):
  """Searches for a code object at the specified line in the specified module.

  Args:
    module: module to explore.
    line: 1-based line number of the statement.

  Returns:
    Code object or None if not found.
  """
  for code_object in _GetModuleCodeObjects(module):
    if native.HasSourceLine(code_object, line):
      return code_object

  return None


def _GetModuleCodeObjects(module):
  """Gets all code objects defined in the specified module.

  Args:
    module: module to explore.

  Returns:
    Set of code objects defined in module.
  """

  if not hasattr(module, '__file__'):
    return set()

  code_objects = set()
  visit_recorder = _VisitRecorder()
  for item in _GetMembers(module):
    _GetCodeObjects(module, item, code_objects, visit_recorder)
  return code_objects


def _GetCodeObjects(module, item, code_objects, visit_recorder):
  """Gets all the code objects deriving from the specified object.

  Only code objects in the specified module are recorded.

  Args:
    module: module to filter the code objects.
    item: class, method or any other element to explore.
    code_objects: target set to record the found code objects.
    visit_recorder: instance of _VisitRecorder class keeping track of already
        visited items to avoid infinite recursion in case of an error.
  """

  def _IsCodeObjectInModule(code_object):
    """Checks if the code object originated from "module".

    If the module was precompiled, the code object may point to .py file, while
    the module says that it originated from .pyc file. We just strip extension
    altogether to work around it.

    Args:
      code_object: code object that we want to check against module.

    Returns:
      True if code_object was implemented in module or false otherwise.
    """
    code_object_file = os.path.splitext(code_object.co_filename)[0]
    module_file = os.path.splitext(module.__file__)[0]
    return code_object_file == module_file

  def _IgnoreClass(cls):
    """Returns true if the class is definitely not coming from "module"."""
    cls_module = sys.modules.get(cls.__module__)
    if not cls_module:
      return False  # We can't tell for sure, so explore this class.

    return (
        cls_module is not module and
        getattr(cls_module, '__file__', None) != module.__file__)

  if not visit_recorder.Record(item):
    return

  if isinstance(item, types.CodeType):
    if not _IsCodeObjectInModule(item):
      return

    code_objects.add(item)

    for const in item.co_consts:
      _GetCodeObjects(module, const, code_objects, visit_recorder)
    return

  if isinstance(item, _METHOD_TYPES):
    if not item.func_code:
      return

    _GetCodeObjects(module, item.func_code, code_objects, visit_recorder)
    return

  if isinstance(item, _CLASS_TYPES):
    if _IgnoreClass(item):
      return

    for class_item in _GetMembers(item):
      _GetCodeObjects(module, class_item, code_objects, visit_recorder)
    return


def _GetMembers(obj):
  """Return all members of an object.

  This function is very similar to inspect.getmembers, but it doesn't return
  object name, doesn't sort and uses iterator syntax. Iterator is more efficient
  in our case, because it doesn't allocate temporary lists.

  Args:
    obj: object to explore (module, class, etc.).

  Yields:
    Item in the object (e.g. method in a class, or class in a module).
  """
  for name in dir(obj):
    try:
      yield getattr(obj, name)
    except AttributeError:
      continue


class _VisitRecorder(object):
  """Helper class to track of already visited objects.

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
      True if the object hasn't been previously visited or first if it has
      already been recorded.
    """

    obj_id = id(obj)
    if obj_id in self._visit_recorder_objects:
      return False

    self._visit_recorder_objects[obj_id] = obj
    return True

