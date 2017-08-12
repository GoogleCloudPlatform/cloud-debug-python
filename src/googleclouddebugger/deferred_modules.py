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
import sys
import time

import cdbg_native as native

# Maximum number of directories that FindModulePath will scan.
_DIRECTORY_LOOKUP_QUOTA = 250


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

