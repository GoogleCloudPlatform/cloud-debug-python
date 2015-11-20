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

"""Computes a unique identifier of the deployed application.

When the application runs under AppEngine, the deployment is uniquely
identified by a minor version string. However when the application runs on
in an unmanaged environment (such as Google Computer Engine virtual machine),
we don't know the version of the application.

We could ignore it, but in the absence of source context, two agents could be
running different versions of the application, but still get bundled as the
same debuggee. This would result in inconsistent behavior when setting
breakpoints.
"""

import os
import sys


# Maximum recursion depth to follow when traversing the file system. This limit
# will prevent stack overflow in case of a loop created by symbolic links.
_MAX_DEPTH = 10


def ComputeApplicationUniquifier(hash_obj):
  """Computes hash of application files.

  Application files can be anywhere on the disk. The application is free to
  import a Python module from an arbitrary path ok the disk. It is also
  impossible to distinguish application files from third party libraries.
  Third party libraries are typically installed with "pip" and there is not a
  good way to guarantee that all instances of the application are going to have
  exactly the same version of each package. There is also a huge amount of files
  in all sys.path directories and it will take too much time to traverse them
  all. We therefore make an assumption that application files are only located
  in sys.path[0].

  When traversing files in sys.path, we can expect both .py and .pyc files. For
  source deployment, we will find both .py and .pyc files. In this case we will
  only index .py files and ignored .pyc file. In case of binary deployment, only
  .pyc file will be there.

  The naive way to hash files would be to read the file content and compute some
  sort of a hash (e.g. SHA1). This can be expensive as well, so instead we just
  hash file name and file size. It is a good enough heuristics to identify
  modified files across different deployments.

  Args:
    hash_obj: hash aggregator to update with application uniquifier.
  """

  def ProcessDirectory(path, relative_path, depth=1):
    """Recursively computes application uniquifier for a particular directory.

    Args:
      path: absolute path of the directory to start.
      relative_path: path relative to sys.path[0]
      depth: current recursion depth.
    """

    if depth > _MAX_DEPTH:
      return

    try:
      names = os.listdir(path)
    except BaseException:
      return

    # Sort file names to ensure consistent hash regardless of order returned
    # by os.listdir. This will also put .py files before .pyc and .pyo files.
    modules = set()
    for name in sorted(names):
      current_path = os.path.join(path, name)
      if not os.path.isdir(current_path):
        file_name, ext = os.path.splitext(name)
        if ext not in ('.py', '.pyc', '.pyo'):
          continue  # This is not an application file.
        if file_name in modules:
          continue  # This is a .pyc file and we already indexed .py file.

        modules.add(file_name)
        ProcessApplicationFile(current_path, os.path.join(relative_path, name))
      elif IsPackage(current_path):
        ProcessDirectory(current_path,
                         os.path.join(relative_path, name),
                         depth + 1)

  def IsPackage(path):
    """Checks if the specified directory is a valid Python package."""
    init_base_path = os.path.join(path, '__init__.py')
    return (os.path.isfile(init_base_path) or
            os.path.isfile(init_base_path + 'c') or
            os.path.isfile(init_base_path + 'o'))

  def ProcessApplicationFile(path, relative_path):
    """Updates the hash with the specified application file."""
    hash_obj.update(relative_path)
    hash_obj.update(':')
    try:
      hash_obj.update(str(os.stat(path).st_size))
    except BaseException:
      pass
    hash_obj.update('\n')

  ProcessDirectory(sys.path[0], '')
