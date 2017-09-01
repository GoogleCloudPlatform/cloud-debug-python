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

"""Provides utility functions for module path processing.
"""


import os


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

