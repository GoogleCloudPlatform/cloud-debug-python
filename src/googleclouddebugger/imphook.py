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

import imp
import os
import sys  # Must be imported, otherwise import hooks don't work.
import threading

# Callbacks to invoke when a module is imported.
_import_callbacks = {}
_import_callbacks_lock = threading.Lock()

# Module fully qualified names detected by the finder at first-time load.
_import_loading_modules = set()

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


class MetaFinder(object):
  """The finder is called with the full module name before it is loaded."""

  def find_module(self, name, path=None):  # pylint: disable=unused-argument,invalid-name
    # Store the module fullname to be used by the import hook.
    # At the time of this call the module is not loaded yet, and is only called
    # the first time the module is loaded. For example, the following statement
    # 'from a.b import c' will make 3 calls to find_module, assuming that none
    # were loaded yet, with the names 'a', 'a.b' and 'a.b.c'
    #
    # Moreover, name might not be a true module name. Example: module 'b' in
    # package 'a' calls 'import c', but 'c' is not a submodule of 'a'. The
    # loader searches for relative submodules first and calls with name='a.c'.
    # Then, it looks for modules on the search path and calls with name='c'.
    # This code adds both 'a.c' and 'c' to the set. However, the import hook
    # handles this case by looking up the module name in sys.modules.
    _import_loading_modules.add(name)
    return None


def _InstallImportHook():
  """Lazily installs import hook."""

  global _real_import

  if _real_import:
    return  # Import hook already installed

  builtin = sys.modules['__builtin__']

  _real_import = getattr(builtin, '__import__')
  assert _real_import

  builtin.__import__ = _ImportHook
  sys.meta_path.append(MetaFinder())


# pylint: disable=redefined-builtin, g-doc-args, g-doc-return-or-yield
def _ImportHook(name, globals=None, locals=None, fromlist=None, level=-1):
  """Callback when a module is being imported by Python interpreter.

  Argument names have to exactly match those of __import__. Otherwise calls
  to __import__ that use keyword syntax will fail: __import('a', fromlist=[]).
  """

  # Really import modules.
  module = _real_import(name, globals, locals, fromlist, level)

  # Optimize common code path when no breakponts are set.
  if not _import_callbacks:
    _import_loading_modules.clear()
    return module

  # Capture and clear the loading module names.
  imp.acquire_lock()
  loaded = frozenset(_import_loading_modules)
  _import_loading_modules.clear()
  imp.release_lock()

  # Invoke callbacks for the loaded modules.
  for m in loaded:
    _InvokeImportCallback(sys.modules.get(m))

  return module


def _InvokeImportCallback(module):
  """Invokes import callbacks for the specified module."""

  if not module:
    return

  path = getattr(module, '__file__', None)
  if not path:
    return

  path, unused_ext = os.path.splitext(os.path.abspath(path))
  callbacks = _import_callbacks.get(path)
  if not callbacks:
    return  # Common code path.

  # Clone the callbacks set, since it can change during enumeration.
  for callback in callbacks.copy():
    callback(module)
