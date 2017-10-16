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

"""Support for breakpoints on modules that haven't been loaded yet."""

import os
import sys  # Must be imported, otherwise import hooks don't work.
import threading

import module_utils

# Callbacks to invoke when a module is imported.
_import_callbacks = {}
_import_callbacks_lock = threading.Lock()

# Per thread data holding information about the import call nest level.
_import_local = threading.local()

# Original __import__ function if import hook is installed or None otherwise.
_real_import = None


def AddImportCallback(abspath, callback):
  """Register import hook.

  This function overrides the default import process. Then whenever a module
  corresponding to source_path is imported, the callback will be invoked.

  A module may be imported multiple times. Import event only means that the
  Python code contained an "import" statement. The actual loading and
  initialization of a new module normally happens only once, at which time
  the callback will be invoked. This function does not validates the existence
  of such a module and it's the responsibility of the caller.

  TODO(erezh): handle module reload.

  Args:
    abspath: python module file absolute path.
    callback: callable to invoke upon module load.

  Returns:
    Function object to invoke to remove the installed callback.
  """

  def RemoveCallback():
    # This is a read-if-del operation on _import_callbacks. Lock to prevent
    # callbacks from being inserted just before the key is deleted. Thus, it
    # must be locked also when inserting a new entry below. On the other hand
    # read only access, in the import hook, does not require a lock.
    with _import_callbacks_lock:
      callbacks = _import_callbacks.get(path)
      if callbacks:
        callbacks.remove(callback)
        if not callbacks:
          del _import_callbacks[path]

  path, unused_ext = os.path.splitext(abspath)
  with _import_callbacks_lock:
    _import_callbacks.setdefault(path, set()).add(callback)
  _InstallImportHook()

  return RemoveCallback


def _InstallImportHook():
  """Lazily installs import hook."""

  global _real_import

  if _real_import:
    return  # Import hook already installed

  builtin = sys.modules['__builtin__']

  _real_import = getattr(builtin, '__import__')
  assert _real_import

  builtin.__import__ = _ImportHook


# pylint: disable=redefined-builtin, g-doc-args, g-doc-return-or-yield
def _ImportHook(name, globals=None, locals=None, fromlist=None, level=-1):
  """Callback when a module is being imported by Python interpreter.

  Argument names have to exactly match those of __import__. Otherwise calls
  to __import__ that use keyword syntax will fail: __import('a', fromlist=[]).
  """

  # This is the top call to import (no nesting), init the per-thread nest level.
  if getattr(_import_local, 'nest_level', None) is None:
    _import_local.nest_level = 0

  _import_local.nest_level += 1

  try:
    # Really import modules.
    module = _real_import(name, globals, locals, fromlist, level)
  finally:
    _import_local.nest_level -= 1

  # No need to invoke the callbacks on nested import calls.
  if _import_local.nest_level:
    return module

  # Optimize common code path when no breakponts are set.
  if not _import_callbacks:
    return module

  _InvokeImportCallback()
  return module


def _InvokeImportCallback():
  """Invokes import callbacks for loaded modules."""
  for path, callbacks in _import_callbacks.items():
    module = module_utils.GetLoadedModuleByPath(path)
    if module:
      for callback in callbacks.copy():
        callback(module)
