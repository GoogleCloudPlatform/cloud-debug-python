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

# Callbacks to invoke when a module is imported.
_import_callbacks = {}
_import_callbacks_lock = threading.Lock()

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

  # Really import modules.
  module = _real_import(name, globals, locals, fromlist, level)

  # Optimize common code path when no breakponts are set.
  if not _import_callbacks:
    return module

  # When the _real_import statement above is executed, it can also trigger the
  # loading of outer packages, if they are not loaded yet. Unfortunately,
  # _real_import does not give us a list of packages/modules were loaded as
  # a result of executing it. Therefore, we conservatively assume that they
  # were all just loaded.
  #
  # To manually identify all modules that _real_import touches, we apply a
  # method that combines 'module', 'name', and 'fromlist'. This method is a
  # heuristic that is based on observation.
  #
  # Note that the list we obtain will contain false positives, i.e., modules
  # that were already loaded. However, since these modules were already loaded,
  # there can be no pending breakpoint callbacks on them, and therefore, the
  # wasted computation will be limited to one dictionary lookup per module.
  #
  # Example: When module 'a.b.c' is imported, we need to activate deferred
  # breakpoints in all of ['a', 'a.b', 'a.b.c']. If 'a' was already loaded, then
  # _import_callbacks.get('a') will return nothing, and we will move on to
  # 'a.b'.
  #
  # To make the code simpler, we keep track of parts of the innermost module
  # (i.e., 'a', 'b', 'c') and then combine them later.

  parts = module.__name__.split('.')
  if fromlist:
    # In case of 'from x import y', all modules in 'fromlist' can be directly
    # found in the package identified by the returned 'module'.
    # Note that we discard the 'name' field, because it is a substring of the
    # name of the returned module.

    # Example 1: Using absolute path.
    #     from a.b import c
    #     name = 'a.b', fromlist=['c'], module=<module 'a.b'>
    #
    # Example 2: Using relative path from inside package 'a'.
    #     from b import c
    #     name = 'b', fromlist=['c'], module=<module 'a.b'>
    #
    # Example 3: Using relative path from inside package 'a'.
    #    from b.c import d
    #    name = 'b.c', fromlist=['d'], module=<module 'a.b.c'>
    pass
  else:
    # In case of 'import a.b', we append the 'name' field to the name of the
    # returned module. Note that these two have one component in common, so
    # we remove that one component from the start of 'name' before appending it.

    # Example 1: Use absolute path.
    #    import a
    #    name = 'a', fromlist=None, module=<module 'a'>
    #
    # Example 2: Use absolute path.
    #    import a.b
    #    name = 'a.b', fromlist=None, module=<module 'a'>
    #
    # Example 3: Use absolute path.
    #    import a.b.c.d
    #    name = 'a.b.c.d', fromlist=None, module=<module 'a'>
    #
    # Example 4: Use relative path from inside package 'a'.
    #    import b.c
    #    name = 'b.c', fromlist=None, module='a.b'
    parts += name.split('.')[1:]

  def GenerateModules():
    """Generates module names using parts and fromlist."""
    # If parts contains ['a', 'b', 'c'], then we generate ['a', 'a.b','a.b.c'].
    current = None
    for part in parts:
      current = (current + '.' + part) if current else part
      yield current

    # We then add entries in fromlist to the final package path (i.e., 'a.b.c')
    # to obtain the innermost packages (i.e., 'a.b.c.d, a.b.c.e').
    if fromlist:
      for f in fromlist:
        yield current + '.' + f

  for m in GenerateModules():
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
