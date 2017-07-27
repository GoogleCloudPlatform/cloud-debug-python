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

"""Finds the loaded module by source path.

The lookup is a fuzzy one, the source path coming from a breakpoint might
be a subpath of module path or may be longer than the module path.
"""

import os
import sys


def FindModules(source_path):
  """Finds the loaded modules whose paths match the given source_path best.

  If there are multiple possible matches, returns them all.

  Args:
    source_path: source file path as specified in the breakpoint.

  Returns:
    List of module objects that best match the source_path or [] if no
    match is found.
  """
  # The lookup is performed in two steps. First, we search all modules whose
  # name match the given source_path's file name (i.e., ignore the leading
  # directory). Then, we select the results whose directory matches the given
  # input best.
  dirname, basename = os.path.split(source_path)
  file_name_root, ext = os.path.splitext(basename)
  if ext != '.py':
    return []  # ".py" extension is expected

  candidates = _GetModulesByFileName(file_name_root)
  if not candidates:
    return []

  if len(candidates) == 1:
    return candidates

  if not dirname:
    return candidates  # No need disambiguate.

  # Find the module that has the best path prefix.
  indices = _Disambiguate(
      dirname,
      [os.path.dirname(module.__file__) for module in candidates])
  return [candidates[i] for i in indices]


def _GetModulesByFileName(lookup_file_name_root):
  """Gets list of all the loaded modules by file name (ignores directory)."""
  matches = []

  # Clone modules dictionaries to allow new modules to load during iteration.
  for unused_name, module in sys.modules.copy().iteritems():
    if not hasattr(module, '__file__'):
      continue  # This is a built-in module.

    file_name_root, ext = os.path.splitext(os.path.basename(module.__file__))

    # TODO(emrekultursay): Verify why we are discarding .pyo files here.
    if (file_name_root == lookup_file_name_root and
        (ext == '.py' or ext == '.pyc')):
      matches.append(module)

  return matches


def _Disambiguate(lookup_path, paths):
  """Disambiguates multiple candidates based on the longest suffix.

  Example when this disambiguation is needed:
    Breakpoint at: 'myproject/app/db/common.py'
    Candidate modules: ['/home/root/fe/common.py', '/home/root/db/common.py']

  In this example the input to this function will be:
    lookup_path = 'myproject/app/db'
    paths = ['/home/root/fe', '/home/root/db']

  The second path is clearly the best match, so this function will return [1].

  Args:
    lookup_path: the source path of the searched module (without file name
        and extension). Must be non-empty.
    paths: candidate paths (each without file name and extension). Must have
        two or more elements.

  Returns:
    List of indices of the best matches.
  """
  assert lookup_path
  assert len(paths) > 1

  best_indices = []
  best_len = 1  # zero-length matches should be discarded.

  for i, path in enumerate(paths):
    current_len = _CommonSuffix(lookup_path, path)

    if current_len > best_len:
      best_indices = [i]
      best_len = current_len
    elif current_len == best_len:
      best_indices.append(i)

  return best_indices


def _CommonSuffix(path1, path2):
  """Computes the number of common directory names at the tail of the paths.

  Examples:
    * _CommonSuffix('a/x/y', 'b/x/y') = 2
    * _CommonSuffix('a/b/c', 'd/e/f') = 0
    * _CommonSuffix('a/b/c', 'a/b/x') = 0

  Args:
    path1: first directory path (should not have file name).
    path2: second directory path (should not have file name).

  Returns:
    Number of common consecutive directory segments from right.
  """

  # Normalize the paths just to be on the safe side
  path1 = path1.strip(os.sep)
  path2 = path2.strip(os.sep)

  counter = 0
  while path1 and path2:
    path1, cur1 = os.path.split(path1)
    path2, cur2 = os.path.split(path2)

    if cur1 != cur2 or not cur1:
      break

    counter += 1

  return counter
