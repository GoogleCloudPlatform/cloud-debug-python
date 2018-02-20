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

"""Provides utility functions for module path processing."""

import os
import sys

from six.moves import xrange  # pylint: disable=redefined-builtin


def GetAbsolutePath(mod_path):
  """Flattens symlinks and indirections in the module path.

  To uniquely identify each module file, the file path must be sanitized
  by following all symbolic links and normalizing to an absolute path.

  Note that the module file (i.e., .py/.pyc/.pyo file) itself can be a
  symbolic link, but we must *NOT* follow that symbolic link.

  Args:
    mod_path: A path that represents a module file.

  Returns:
    The sanitized version of mod_path.
  """
  pkg_path, file_name = os.path.split(mod_path)
  pkg_path = os.path.abspath(os.path.realpath(pkg_path))
  return os.path.join(pkg_path, file_name)


def GetLoadedModuleByPath(abspath):
  """Returns the loaded module that matches abspath or None if not found."""

  def GenModuleNames(path):
    """Generates all possible module names from path."""
    parts = path.lstrip(os.sep).split(os.sep)

    # For packages, remove the __init__ file name.
    if parts[-1] == '__init__':
      parts = parts[:-1]

    # Generate module names from part, starting with just the leaf name.
    for i in xrange(len(parts) - 1, -1, -1):
      yield '.'.join(parts[i::])

    # If non where matching, it is possible that it's the main module.
    yield '__main__'

  # The extenssion is not part of the module matching, remove it.
  abspath = os.path.splitext(abspath)[0]

  # Lookup every possible module name for abspath, starting with the leaf name.
  # It is much faster than scanning sys.modules and comparing module paths.
  for mod_name in GenModuleNames(abspath):
    module = sys.modules.get(mod_name)
    if not module:
      continue

    mod_path = getattr(module, '__file__', None)
    if not mod_path:
      continue

    # Get the absolute real path (no symlink) for this module.
    mod_path = os.path.splitext(GetAbsolutePath(mod_path))[0]
    if mod_path == abspath:
      return module
