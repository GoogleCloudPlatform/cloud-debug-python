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
import time

import cdbg_native as native

# Maximum number of directories that FindModulePath will scan.
_DIRECTORY_LOOKUP_QUOTA = 250

# Callbacks to invoke when a module is imported.
_import_callbacks = {}
_import_callbacks_lock = threading.Lock()

# Module fully qualified names detected by the finder at first-time load.
_import_loading_modules = set()

# Original __import__ function if import hook is installed or None otherwise.
_real_import = None


# TODO(emrekultursay): Move this method out of deferred_modules.py file.
def FindModulePath(source_path):
  """Checks availability of a Python module.

  This function checks if it is possible that a module (loaded or not)
  will match the specified path.

  There is no absolutely correct way to do this. The application may just
  import a module from a string, or dynamically change sys.path. This function
  implements heuristics that should cover all reasonable cases with a good
  performance.

  There can be some edge cases when this code is going to scan a huge number
  of directories. This can be very expensive. To mitigate it, we limit the
  number of directories that can be scanned. If this threshold is reached,
  false negatives (i.e., missing modules in the output) are possible.

  Args:
    source_path: source path as specified in the breakpoint.

  Returns:
    A list containing the paths of modules that best match source_path.
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

  # For packages, module_name will be the name of the package (e.g., for
  # 'a/b/c/__init__.py' it will be 'c'). Otherwise, module_name will be the
  # name of the module (e.g., for 'a/b/c/foo.py' it will be 'foo').
  module_name = _GetModuleName(source_path)
  if not module_name:
    return []

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

  # Normalize paths and remove duplicates.
  paths = set(os.path.abspath(path) for path in paths)

  best_match = _FindBestMatch(source_path, module_name, paths)

  native.LogInfo(
      ('Look up for %s completed in %d directories, '
       'scanned %d directories (quota: %d), '
       'result: %r, total time: %f ms') % (
           module_name,
           len(paths),
           directory_lookups[0],
           _DIRECTORY_LOOKUP_QUOTA,
           best_match,
           (time.time() - start_time) * 1000))
  return sorted(best_match)


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
    callback()


# TODO(emrekultursay): Try reusing the Disambiguate method in module_lookup.py.
def _FindBestMatch(source_path, module_name, paths):
  """Returns paths entries that have longest suffix match with source_path."""
  best = []
  best_suffix_len = 0
  for path in paths:
    try:
      fp, p, unused_d = imp.find_module(module_name, [path])

      # find_module may return relative path (relative to current directory),
      # which requires normalization.
      p = os.path.abspath(p)

      # find_module returns fp=None when it finds a package, in which case we
      # should be finding common suffix against __init__.py in that package.
      if not fp:
        p = os.path.join(p, '__init__.py')
      else:
        fp.close()

      suffix_len = _CommonSuffix(source_path, p)

      if suffix_len > best_suffix_len:
        best = [p]
        best_suffix_len = suffix_len
      elif suffix_len == best_suffix_len:
        best.append(p)

    except ImportError:
      pass  # a module with the given name was not found inside path.

  return best


# TODO(emrekultursay): Remove duplicate copy in module_lookup.py.
def _CommonSuffix(path1, path2):
  """Returns the number of common directory names at the tail of the paths."""
  return len(os.path.commonprefix([
      path1[::-1].split(os.sep),
      path2[::-1].split(os.sep)]))

