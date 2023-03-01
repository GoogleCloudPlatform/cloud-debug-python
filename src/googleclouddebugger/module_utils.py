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

def NormalizePath(path):
  """Normalizes a path.

  E.g. One example is it will convert "/a/b/./c" -> "/a/b/c"
  """
  # TODO: Calling os.path.normpath "may change the meaning of a
  # path that contains symbolic links" (e.g., "A/foo/../B" != "A/B" if foo is a
  # symlink). This might cause trouble when matching against loaded module
  # paths. We should try to avoid using it.
  # Example:
  #  > import symlink.a
  #  > symlink.a.__file__
  #  symlink/a.py
  #  > import target.a
  #  > starget.a.__file__
  #  target/a.py
  # Python interpreter treats these as two separate modules. So, we also need to
  # handle them the same way.
  return os.path.normpath(path)


def IsPathSuffix(mod_path, path):
  """Checks whether path is a full path suffix of mod_path.

  Args:
    mod_path: Must be an absolute path to a source file. Must not have
              file extension.
    path: A relative path. Must not have file extension.

  Returns:
    True if path is a full path suffix of mod_path. False otherwise.
  """
  return (mod_path.endswith(path) and (len(mod_path) == len(path) or
                                       mod_path[:-len(path)].endswith(os.sep)))


def GetLoadedModuleBySuffix(path):
  """Searches sys.modules to find a module with the given file path.

  Args:
    path: Path to the source file. It can be relative or absolute, as suffix
          match can handle both. If absolute, it must have already been
          sanitized.

  Algorithm:
    The given path must be a full suffix of a loaded module to be a valid match.
    File extensions are ignored when performing suffix match.

  Example:
    path: 'a/b/c.py'
    modules: {'a': 'a.py', 'a.b': 'a/b.py', 'a.b.c': 'a/b/c.pyc']
    returns: module('a.b.c')

  Returns:
    The module that corresponds to path, or None if such module was not
    found.
  """
  root = os.path.splitext(path)[0]
  for module in sys.modules.values():
    mod_root = os.path.splitext(getattr(module, '__file__', None) or '')[0]

    if not mod_root:
      continue

    # While mod_root can contain symlinks, we cannot eliminate them. This is
    # because, we must perform exactly the same transformations on mod_root and
    # path, yet path can be relative to an unknown directory which prevents
    # identifying and eliminating symbolic links.
    #
    # Therefore, we only convert relative to absolute path.
    if not os.path.isabs(mod_root):
      mod_root = os.path.join(os.getcwd(), mod_root)

    # In the following invocation 'python3 ./main.py' (using the ./), the
    # mod_root variable will '/base/path/./main'. In order to correctly compare
    # it with the root variable, it needs to be '/base/path/main'.
    mod_root = NormalizePath(mod_root)

    if IsPathSuffix(mod_root, root):
      return module

  return None
