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

"""Inclusive search for module files."""

import os
import sys


def Search(path):
  """Search sys.path to find a source file that matches path.

  The provided input path may have an unknown number of irrelevant outer
  directories (e.g., /garbage1/garbage2/real1/real2/x.py').  This function
  does multiple search iterations until an actual Python module file that
  matches the input path is found. At each iteration, it strips one leading
  directory from the path and searches the directories at sys.path
  for a match.

  Examples:
    sys.path: ['/x1/x2', '/y1/y2']
    Search order: [.pyo|.pyc|.py]
      /x1/x2/a/b/c
      /x1/x2/b/c
      /x1/x2/c
      /y1/y2/a/b/c
      /y1/y2/b/c
      /y1/y2/c
    Filesystem: ['/y1/y2/a/b/c.pyc']

    1) Search('a/b/c.py')
         Returns '/y1/y2/a/b/c.pyc'
    2) Search('q/w/a/b/c.py')
         Returns '/y1/y2/a/b/c.pyc'
    3) Search('q/w/c.py')
         Returns 'q/w/c.py'

    The provided input path may also be relative to an unknown directory.
    The path may include some or all outer package names.

  Examples (continued):

    4) Search('c.py')
         Returns 'c.py'
    5) Search('b/c.py')
         Returns 'b/c.py'

  Args:
    path: Path that describes a source file. Must contain .py file extension.
          Must not contain any leading os.sep character.

  Returns:
    Full path to the matched source file, if a match is found. Otherwise,
    returns the input path.

  Raises:
    AssertionError: if the provided path is an absolute path, or if it does not
      have a .py extension.
  """
  def SearchCandidates(p):
    """Generates all candidates for the fuzzy search of p."""
    while p:
      yield p
      (_, _, p) = p.partition(os.sep)

  # Verify that the os.sep is already stripped from the input.
  assert not path.startswith(os.sep)

  # Strip the file extension, it will not be needed.
  src_root, src_ext = os.path.splitext(path)
  assert src_ext == '.py'

  # Search longer suffixes first. Move to shorter suffixes only if longer
  # suffixes do not result in any matches.
  for src_part in SearchCandidates(src_root):
    # Search is done in sys.path order, which gives higher priority to earlier
    # entries in sys.path list.
    for sys_path in sys.path:
      f = os.path.join(sys_path, src_part)
      # The order in which we search the extensions does not matter.
      for ext in ('.pyo', '.pyc', '.py'):
        # The os.path.exists check internally follows symlinks and flattens
        # relative paths, so we don't have to deal with it.
        fext = f + ext
        if os.path.exists(fext):
          # Once we identify a matching file in the filesystem, we should
          # preserve the (1) potentially-symlinked and (2)
          # potentially-non-flattened file path (f+ext), because that's exactly
          # how we expect it to appear in sys.modules when we search the file
          # there.
          return fext

  # A matching file was not found in sys.path directories.
  return path

