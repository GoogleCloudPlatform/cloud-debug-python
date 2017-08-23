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
import pkgutil
import sys
import time

import cdbg_native as native


def _CommonPathSuffixLen(paths):
  """Returns the longest common path suffix len in a list of paths."""
  return len(os.path.commonprefix([path[::-1].split(os.sep) for path in paths]))


def _GetIsPackageAndModuleName(path_noext):
  """Returns a tuple indicating whether the path is a package and a name."""

  directory, name = os.path.split(path_noext)
  if name != '__init__':
    return (False, name)
  # It is a package, return the package name.
  return (True, os.path.basename(directory))


# TODO(erezh): Ensure we handle whitespace in paths correctly including,
# extension, basename and dirname.
def FindMatchingFiles(location_path):
  """Returns a list of absolute filenames of best matching modules/packages."""

  def AddCandidate(mod_path):
    suffix_len = _CommonPathSuffixLen([src_path, mod_path])
    if suffix_len < longest_suffix_len[0]:
      return
    if suffix_len > longest_suffix_len[0]:
      candidates.clear()
      longest_suffix_len[0] = suffix_len
    candidates.add(mod_path)

  # We measure the time it takes to execute the scan.
  start_time = time.time()
  num_dirs_scanned = 0

  # Remove the file extension and identify if it's a package.
  src_path, src_ext = os.path.splitext(location_path)
  assert src_ext == '.py'
  (src_ispkg, src_name) = _GetIsPackageAndModuleName(src_path)
  assert src_name

  # Using mutable vars to make them available in nested functions.

  # The set of module/package path w/ no extension. Contains absolute paths.
  candidates = set()

  # Init longest_suffix_len to 1 to avoid inserting zero length suffixes.
  longest_suffix_len = [1]

  # Search paths for modules and packages, init with system search paths.
  search_abspaths = set(os.path.abspath(path) for path in sys.path)

  # Add search paths from the already loaded packages and add matching modules
  # or packages to the candidates list.
  for module in sys.modules.values():
    # Extend the search paths with packages path and modules file directory.
    # Note that __path__ only exist for packages, but does not have to be
    # absolute path as the user can overwrite it.
    search_abspaths |= frozenset(
        os.path.abspath(path) for path in getattr(module, '__path__', []))
    mod_path = os.path.splitext(getattr(module, '__file__', ''))[0]
    if not mod_path:
      continue
    mod_abspath = os.path.abspath(mod_path)
    search_abspaths.add(os.path.dirname(mod_abspath))
    # Add loaded modules to the candidates set.
    if (src_ispkg, src_name) == _GetIsPackageAndModuleName(mod_abspath):
      AddCandidate(mod_abspath)

  # Walk the aggregated search path and loook for modules or packages.
  # By searching one path at the time we control the module file name
  # without having to load it.
  # TODO(erezh): consider using the alternative impl in cr/165133821 which
  # only uses os file lookup and not using pkgutil. The alternative is faster
  # but is making many more assuptions that this impl does not.
  while search_abspaths:
    num_dirs_scanned += 1
    abspath = search_abspaths.pop()
    for unused_importer, mod_name, mod_ispkg in pkgutil.iter_modules([abspath]):
      mod_abspath = os.path.join(abspath, mod_name)
      if mod_ispkg:
        search_abspaths.add(mod_abspath)
        mod_abspath = os.path.join(mod_abspath, '__init__')
      if src_ispkg == mod_ispkg and src_name == mod_name:
        AddCandidate(mod_abspath)

  # Sort the list to return a stable result to the user.
  # TODO(erezh): No need to add the .py extenssion, this is done just for
  # compatabilty with current code. Once refactored not to use file extension
  # this code can be removed to just return the sorted candidates.
  candidates = sorted(abspath + '.py' for abspath in candidates)

  # Log scan stats, without the files list to avoid very long output as well as
  # the potential leak of system files that the user has no access to.
  native.LogInfo(
      ('Found %d files matching \'%s\' in %d scanned folders in %f ms') % (
          len(candidates),
          location_path,
          num_dirs_scanned,
          (time.time() - start_time) * 1000))

  # Return a sorted result for stable report to the user
  return candidates
