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
import time

import cdbg_native as native

# Maximum number of directories that IsValidSourcePath will scan.
_DIRECTORY_LOOKUP_QUOTA = 250

# Callbacks to invoke when a module is imported.
_import_callbacks = {}

# Original __import__ function if import hook is installed or None otherwise.
_real_import = None


def IsValidSourcePath(source_path):
  """Checks availability of a Python module.

  This function checks if it is possible that a module will match the specified
  path. We only use the file name and we ignore the directory.

  There is no absolutely correct way to do this. The application may just
  import a module from a string, or dynamically change sys.path. This function
  implements heuristics that should cover all reasonable cases with a good
  performance.

  There can be some edge cases when this code is going to scan a huge number
  of directories. This can be very expensive. To mitigate it, we limit the
  number of directories that can be scanned. If this threshold is reached,
  false negatives are possible.

  Args:
    source_path: source path as specified in the breakpoint.

  Returns:
    True if it is possible that a module matching source_path will ever be
    loaded or false otherwise.
  """

  def IsPackage(path):
    """Checks if the specified directory is a valid Python package."""
    init_base_path = os.path.join(path, '__init__.py')
    return (os.path.isfile(init_base_path) or
            os.path.isfile(init_base_path + 'c') or
            os.path.isfile(init_base_path + 'o'))

  def SubPackages(path):
    """Gets a list of all the directories of subpackages of path."""
    if os.path.isdir(path):
      for name in os.listdir(path):
        if '.' in name:
          continue  # This is definitely a file, package names can't have dots.

        if directory_lookups[0] >= _DIRECTORY_LOOKUP_QUOTA:
          break

        directory_lookups[0] += 1

        subpath = os.path.join(path, name)
        if IsPackage(subpath):
          yield subpath

  start_time = time.time()
  directory_lookups = [0]

  file_name = _GetModuleName(source_path)
  if not file_name:
    return False

  # Recursively discover all the subpackages in all the Python paths.
  paths = set()
  pending = set(sys.path)
  while pending:
    path = pending.pop()
    paths.add(path)
    pending |= frozenset(SubPackages(path)) - paths

  # Append all directories where some modules have already been loaded. There
  # is a good chance that the file we are looking for will be there. This is
  # only useful if a module got somehow loaded outside of sys.path. We don't
  # include these paths in the recursive discovery of subpackages because it
  # takes a lot of time in some edge cases and not worth it.
  default_path = sys.path[0]
  for unused_module_name, module in sys.modules.copy().iteritems():
    file_path = getattr(module, '__file__', None)
    path, unused_name = os.path.split(file_path) if file_path else (None, None)
    paths.add(path or default_path)

  try:
    imp.find_module(file_name, list(paths))
    rc = True
  except ImportError:
    rc = False

  native.LogInfo(
      ('Look up for %s completed in %d directories, '
       'scanned %d directories (quota: %d), '
       'result: %r, total time: %f ms') % (
           file_name,
           len(paths),
           directory_lookups[0],
           _DIRECTORY_LOOKUP_QUOTA,
           rc,
           (time.time() - start_time) * 1000))
  return rc


def AddImportCallback(source_path, callback):
  """Register import hook.

  This function overrides the default import process. Then whenever a module
  corresponding to source_path is imported, the callback will be invoked.

  A module may be imported multiple times. Import event only means that the
  Python code contained an "import" statement. The actual loading and
  initialization of a new module normally happens only once. After that the
  module is just fetched from the cache. This function doesn't care whether a
  module was loaded or fetched from cache. The callback will be triggered
  all the same.

  Args:
    source_path: source file path identifying the monitored module name. If
        the file is __init__.py, this function will monitor package import.
        Otherwise it will monitor module import.
    callback: callable to invoke upon module import.

  Returns:
    Function object to invoke to remove the installed callback.
  """

  def RemoveCallback():
    # Atomic operations, no need to lock.
    callbacks = _import_callbacks.get(module_name)
    if callbacks:
      callbacks.remove(callback)

  module_name = _GetModuleName(source_path)
  if not module_name:
    return None

  # Atomic operations, no need to lock.
  _import_callbacks.setdefault(module_name, set()).add(callback)
  _InstallImportHook()

  return RemoveCallback


def _GetModuleName(source_path):
  """Gets the name of the module that corresponds to source_path.

  Args:
    source_path: file path to resolve into a module.

  Returns:
    If the source file is __init__.py, this function will return the name
    of the package (last directory before file name). Otherwise this function
    return file name without extension.
  """
  directory, name = os.path.split(source_path)
  if name == '__init__.py':
    if not directory.strip(os.sep):
      return None  # '__init__.py' is way too generic. We can't match it.

    directory, file_name = os.path.split(directory)
  else:
    file_name, ext = os.path.splitext(name)
    if ext != '.py':
      return None  # ".py" extension is expected

  return file_name


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

  module = _real_import(name, globals, locals, fromlist, level)

  # Invoke callbacks for the imported module. No need to lock, since all
  # operations are atomic.
  pos = name.rfind('.') + 1
  _InvokeImportCallback(name[pos:])

  if fromlist:
    for module_name in fromlist:
      _InvokeImportCallback(module_name)

  return module


def _InvokeImportCallback(module_name):
  """Invokes import callbacks for the specified module."""
  callbacks = _import_callbacks.get(module_name)
  if not callbacks:
    return  # Common code path.

  # Clone the callbacks set, since it can change during enumeration.
  for callback in callbacks.copy():
    callback(module_name)

