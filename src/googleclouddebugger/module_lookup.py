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


def FindModule(source_path):
  """Find the loaded module by source path.

  If there are multiple possible matches, chooses the best match.

  Args:
    source_path: source file path as specified in the breakpoint.

  Returns:
    Module object that best matches the source_path or None if no match found.
  """
  file_name, ext = os.path.splitext(os.path.basename(source_path))
  if ext != '.py':
    return None  # ".py" extension is expected

  candidates = _GetModulesByFileName(file_name)
  if not candidates:
    return None

  if len(candidates) == 1:
    return candidates[0]

  return candidates[_Disambiguate(
      os.path.split(source_path)[0],
      [os.path.split(module.__file__)[0] for module in candidates])]


def _GetModulesByFileName(lookup_file_name):
  """Gets list of all the loaded modules by file name (ignores directory)."""
  matches = []

  # Clone modules dictionaries to allow new modules to load during iteration.
  for unused_name, module in sys.modules.copy().iteritems():
    if not hasattr(module, '__file__'):
      continue  # This is a built-in module.

    file_name, ext = os.path.splitext(os.path.basename(module.__file__))
    if file_name == lookup_file_name and (ext == '.py' or ext == '.pyc'):
      matches.append(module)

  return matches


def _Disambiguate(lookup_path, paths):
  """Disambiguate multiple candidates based on the longest suffix.

  Example when this disambiguation is needed:
    Breakpoint at: 'myproject/app/db/common.py'
    Candidate modules: ['/home/root/fe/common.py', '/home/root/db/common.py']

  In this example the input to this function will be:
    lookup_path = 'myproject/app/db'
    paths = ['/home/root/fe', '/home/root/db']

  The second path is clearly the best match, so this function will return 1.

  Args:
    lookup_path: the source path of the searched module (without file name
        and extension).
    paths: candidate paths (each without file name and extension).

  Returns:
    Index of the best match or arbitrary index if this function can't
    discriminate.
  """
  best_index = 0
  best_len = 0
  for i in range(len(paths)):
    current_len = _CommonSuffix(lookup_path, paths[i])
    if current_len > best_len:
      best_index = i
      best_len = current_len

  return best_index


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
