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

"""Support for breakpoints on modules that haven't been loaded yet.

This is the new module import hook which:
  1. Takes a partial path of the module file excluding the file extension as
     input (can be as short as 'foo' or longer such as 'sys/path/pkg/foo').
  2. At each (top-level-only) import statement:
    a. Generates an estimate of the modules that might be loaded as a result
       of this import (and all chained imports) using the arguments of the
       import hook. The estimate is best-effort, it may contain extra entries
       that are not of interest to us (e.g., outer packages that were already
       loaded before this import), or may be missing some module names (not
       all intricacies of Python module importer are handled).
    b. Checks sys.modules if any of these modules have a file that matches the
       given path, using suffix match.

For the old module import hook, see imphook.py file.
"""

import importlib
import itertools
import os
import sys  # Must be imported, otherwise import hooks don't work.
import threading

import six
from six.moves import builtins  # pylint: disable=redefined-builtin

from . import module_utils2

# Callbacks to invoke when a module is imported.
_import_callbacks = {}
_import_callbacks_lock = threading.Lock()

# Per thread data holding information about the import call nest level.
_import_local = threading.local()

# Original __import__ function if import hook is installed or None otherwise.
_real_import = None

# Original importlib.import_module function if import hook is installed or None
# otherwise.
_real_import_module = None


def AddImportCallbackBySuffix(path, callback):
  """Register import hook.

  This function overrides the default import process. Then whenever a module
  whose suffix matches path is imported, the callback will be invoked.

  A module may be imported multiple times. Import event only means that the
  Python code contained an "import" statement. The actual loading and
  initialization of a new module normally happens only once, at which time
  the callback will be invoked. This function does not validates the existence
  of such a module and it's the responsibility of the caller.

  TODO: handle module reload.

  Args:
    path: python module file path. It may be missing the directories for the
          outer packages, and therefore, requires suffix comparison to match
          against loaded modules. If it contains all outer packages, it may
          contain the sys.path as well.
          It might contain an incorrect file extension (e.g., py vs. pyc).
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

  with _import_callbacks_lock:
    _import_callbacks.setdefault(path, set()).add(callback)
  _InstallImportHookBySuffix()

  return RemoveCallback


def _InstallImportHookBySuffix():
  """Lazily installs import hook."""
  global _real_import

  if _real_import:
    return  # Import hook already installed

  _real_import = getattr(builtins, '__import__')
  assert _real_import
  builtins.__import__ = _ImportHookBySuffix

  if six.PY3:
    # In Python 2, importlib.import_module calls __import__ internally so
    # overriding __import__ is enough. In Python 3, they are separate so it also
    # needs to be overwritten.
    global _real_import_module
    _real_import_module = importlib.import_module
    assert _real_import_module
    importlib.import_module = _ImportModuleHookBySuffix


def _IncrementNestLevel():
  """Increments the per thread nest level of imports."""
  # This is the top call to import (no nesting), init the per-thread nest level
  # and names set.
  if getattr(_import_local, 'nest_level', None) is None:
    _import_local.nest_level = 0

  if _import_local.nest_level == 0:
    # Re-initialize names set at each top-level import to prevent any
    # accidental unforeseen memory leak.
    _import_local.names = set()

  _import_local.nest_level += 1


# pylint: disable=redefined-builtin
def _ProcessImportBySuffix(name, fromlist, globals):
  """Processes an import.

  Calculates the possible names generated from an import and invokes
  registered callbacks if needed.

  Args:
    name: Argument as passed to the importer.
    fromlist: Argument as passed to the importer.
    globals: Argument as passed to the importer.
  """
  _import_local.nest_level -= 1

  # To improve common code path performance, compute the loaded modules only
  # if there are any import callbacks.
  if _import_callbacks:
    # Collect the names of all modules that might be newly loaded as a result
    # of this import. Add them in a thread-local list.
    _import_local.names |= _GenerateNames(name, fromlist, globals)

    # Invoke the callbacks only on the top-level import call.
    if _import_local.nest_level == 0:
      _InvokeImportCallbackBySuffix(_import_local.names)

  # To be safe, we clear the names set every time we exit a top level import.
  if _import_local.nest_level == 0:
    _import_local.names.clear()


# pylint: disable=redefined-builtin, g-doc-args, g-doc-return-or-yield
def _ImportHookBySuffix(
    name, globals=None, locals=None, fromlist=None, level=None):
  """Callback when an import statement is executed by the Python interpreter.

  Argument names have to exactly match those of __import__. Otherwise calls
  to __import__ that use keyword syntax will fail: __import('a', fromlist=[]).
  """
  _IncrementNestLevel()

  if level is None:
    # A level of 0 means absolute import, positive values means relative
    # imports, and -1 means to try both an absolute and relative import.
    # Since imports were disambiguated in Python 3, -1 is not a valid value.
    # The default values are 0 and -1 for Python 3 and 3 respectively.
    # https://docs.python.org/2/library/functions.html#__import__
    # https://docs.python.org/3/library/functions.html#__import__
    level = 0 if six.PY3 else -1

  try:
    # Really import modules.
    module = _real_import(name, globals, locals, fromlist, level)
  finally:
    # This _real_import call may raise an exception (e.g., ImportError).
    # However, there might be several modules already loaded before the
    # exception was raised. For instance:
    #   a.py
    #     import b  # success
    #     import c  # ImportError exception.
    # In this case, an 'import a' statement would have the side effect of
    # importing module 'b'. This should trigger the import hooks for module
    # 'b'. To achieve this, we always search/invoke import callbacks (i.e.,
    # even when an exception is raised).
    #
    # Important Note: Do not use 'return' inside the finally block. It will
    # cause any pending exception to be discarded.
    _ProcessImportBySuffix(name, fromlist, globals)

  return module


def _ResolveRelativeImport(name, package):
  """Resolves a relative import into an absolute path.

  This is mostly an adapted version of the logic found in the backported
  version of import_module in Python 2.7.
  https://github.com/python/cpython/blob/2.7/Lib/importlib/__init__.py

  Args:
    name: relative name imported, such as '.a' or '..b.c'
    package: absolute package path, such as 'a.b.c.d.e'

  Returns:
    The absolute path of the name to be imported, or None if it is invalid.
    Examples:
      _ResolveRelativeImport('.c', 'a.b') -> 'a.b.c'
      _ResolveRelativeImport('..c', 'a.b') -> 'a.c'
      _ResolveRelativeImport('...c', 'a.c') -> None
  """
  level = sum(1 for c in itertools.takewhile(lambda c: c == '.', name))
  if level == 1:
    return package + name
  else:
    parts = package.split('.')[:-(level - 1)]
    if not parts:
      return None
    parts.append(name[level:])
    return '.'.join(parts)


def _ImportModuleHookBySuffix(name, package=None):
  """Callback when a module is imported through importlib.import_module."""
  _IncrementNestLevel()

  try:
    # Really import modules.
    module = _real_import_module(name, package)
  finally:
    if name.startswith('.'):
      if package:
        name = _ResolveRelativeImport(name, package)
      else:
        # Should not happen. Relative imports require the package argument.
        name = None
    if name:
      _ProcessImportBySuffix(name, None, None)

  return module


def _GenerateNames(name, fromlist, globals):
  """Generates the names of modules that might be loaded via this import.

  Args:
    name: Argument as passed to the importer.
    fromlist: Argument as passed to the importer.
    globals: Argument as passed to the importer.

  Returns:
    A set that contains the names of all modules that are loaded by the
    currently executing import statement, as they would show up in sys.modules.
    The returned set may contain module names that were already loaded before
    the execution of this import statement.
    The returned set may contain names that are not real modules.
  """
  def GetCurrentPackage(globals):
    """Finds the name of the package for the currently executing module."""
    if not globals:
      return None

    # Get the name of the module/package that the current import is being
    # executed in.
    current = globals.get('__name__')
    if not current:
      return None

    # Check if the current module is really a module, or a package.
    current_file = globals.get('__file__')
    if not current_file:
      return None

    root = os.path.splitext(os.path.basename(current_file))[0]
    if root == '__init__':
      # The current import happened from a package. Return the package.
      return current
    else:
      # The current import happened from a module. Return the package that
      # contains the module.
      return current.rpartition('.')[0]

  # A Python module can be addressed in two ways:
  #   1. Using a path relative to the currently executing module's path. For
  #   instance, module p1/p2/m3.py imports p1/p2/p3/m4.py using 'import p3.m4'.
  #   2. Using a path relative to sys.path. For instance, module p1/p2/m3.py
  #   imports p1/p2/p3/m4.py using 'import p1.p2.p3.m4'.
  #
  # The Python importer uses the 'globals' argument to identify the module that
  # the current import is being performed in. The actual logic is very
  # complicated, and we only approximate it here to limit the performance
  # overhead (See import.c in the interpreter for details). Here, we only use
  # the value of the globals['__name__'] for this purpose.
  #
  # Note: The Python importer prioritizes the current package over sys.path. For
  # instance, if 'p1.p2.m3' imports 'm4', then 'p1.p2.m4' is a better match than
  # the top level 'm4'. However, the debugger does not have to implement this,
  # because breakpoint paths are not described relative to some other file. They
  # are always assumed to be relative to the sys.path directories. If the user
  # sets breakpoint inside 'm4.py', then we can map it to either the top level
  # 'm4' or 'p1.p2.m4', i.e., both are valid matches.
  curpkg = GetCurrentPackage(globals)

  names = set()

  # A Python module can be imported using two syntaxes:
  #   1. import p1.p2.m3
  #   2. from p1.p2 import m3
  #
  # When the regular 'import p1.p2.m3' syntax is used, the name of the module
  # being imported is passed in the 'name' argument (e.g., name='p1.p2.m3',
  # fromlist=None).
  #
  # When the from-import syntax is used, then fromlist contains the leaf names
  # of the modules, and name contains the containing package. For instance, if
  # name='a.b', fromlist=['c', 'd'], then we add ['a.b.c', 'a.b.d'].
  #
  # Corner cases:
  #   1. The fromlist syntax can be used to import a function from a module.
  #      For instance, 'from p1.p2.m3 import func'.
  #   2. Sometimes, the importer is passed a dummy fromlist=['__doc__'] (see
  #      import.c in the interpreter for details).
  # Due to these corner cases, the returned set may contain entries that are not
  # names of real modules.
  for from_entry in fromlist or []:
    # Name relative to sys.path.
    # For relative imports such as 'from . import x', name will be the empty
    # string. Thus we should not prepend a '.' to the entry.
    entry = (name + '.' + from_entry) if name else from_entry
    names.add(entry)
    # Name relative to the currently executing module's package.
    if curpkg:
      names.add(curpkg + '.' + entry)

  # Generate all names from name. For instance, if name='a.b.c', then
  # we need to add ['a.b.c', 'a.b', 'a'].
  while name:
    # Name relative to sys.path.
    names.add(name)
    # Name relative to currently executing module's package.
    if curpkg:
      names.add(curpkg + '.' + name)
    name = name.rpartition('.')[0]

  return names


def _InvokeImportCallbackBySuffix(names):
  """Invokes import callbacks for newly loaded modules.

  Uses a path suffix match to identify whether a loaded module matches the
  file path provided by the user.

  Args:
    names: A set of names for modules that are loaded by the current import.
           The set may contain some superfluous entries that were already
           loaded before this import, or some entries that do not correspond
           to a module. The list is expected to be much smaller than the exact
           sys.modules so that a linear search is not as costly.
  """
  def GetModuleFromName(name, path):
    """Returns the loaded module for this name/path, or None if not found.

    Args:
      name: A string that may represent the name of a loaded Python module.
      path: If 'name' ends with '.*', then the last path component in 'path' is
            used to identify what the wildcard may map to. Does not contain file
            extension.

    Returns:
      The loaded module for the given name and path, or None if a loaded module
      was not found.
    """
    # The from-import syntax can be used as 'from p1.p2 import *'. In this case,
    # we cannot know what modules will match the wildcard. However, we know that
    # the wildcard can only be used to import leaf modules. So, we guess that
    # the leaf module will have the same name as the leaf file name the user
    # provided. For instance,
    #   User input path = 'foo.py'
    #   Currently executing import:
    #     from pkg1.pkg2 import *
    #   Then, we combine:
    #      1. 'pkg1.pkg2' from import's outer package and
    #      2. Add 'foo' as our guess for the leaf module name.
    #   So, we will search for modules with name similar to 'pkg1.pkg2.foo'.
    if name.endswith('.*'):
      # Replace the final '*' with the name of the module we are looking for.
      name = name.rpartition('.')[0] + '.' + path.split('/')[-1]

    # Check if the module was loaded.
    return sys.modules.get(name)

  # _import_callbacks might change during iteration because RemoveCallback()
  # might delete items. Iterate over a copy to avoid a
  # 'dictionary changed size during iteration' error.
  for path, callbacks in list(_import_callbacks.items()):
    root = os.path.splitext(path)[0]

    nonempty_names = (n for n in names if n)
    modules = (GetModuleFromName(name, root) for name in nonempty_names)
    nonempty_modules = (m for m in modules if m)

    for module in nonempty_modules:
      # TODO: Write unit test to cover None case.
      mod_file = getattr(module, '__file__', None)
      if not mod_file:
        continue
      if not isinstance(mod_file, str):
        continue

      mod_root = os.path.splitext(mod_file)[0]

      # If the module is relative, add the curdir prefix to convert it to
      # absolute path. Note that we don't use os.path.abspath because it
      # also normalizes the path (which has side effects we don't want).
      if not os.path.isabs(mod_root):
        mod_root = os.path.join(os.curdir, mod_root)

      if module_utils2.IsPathSuffix(mod_root, root):
        for callback in callbacks.copy():
          callback(module)
        break
