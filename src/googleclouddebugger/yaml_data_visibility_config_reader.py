# Copyright 2017 Google Inc. All Rights Reserved.
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

"""Reads a YAML configuration file to determine visibility policy.

Example Usage:
  try:
    config = yaml_data_visibility_config_reader.OpenAndRead(filename)
  except yaml_data_visibility_config_reader.Error as e:
    ...

  visibility_policy = GlobDataVisibilityPolicy(
    config.blacklist_patterns,
    config.whitelist_patterns)
"""

import os
import sys
import yaml


class Error(Exception):
  """Generic error class that other errors in this module inherit from."""
  pass


class YAMLLoadError(Error):
  """Thrown when reading an opened file fails."""
  pass


class ParseError(Error):
  """Thrown when there is a problem with the YAML structure."""
  pass


class UnknownConfigKeyError(Error):
  """Thrown when the YAML contains an unsupported keyword."""
  pass


class NotAListError(Error):
  """Thrown when a YAML key does not reference a list."""
  pass


class ElementNotAStringError(Error):
  """Thrown when a YAML list element is not a string."""
  pass


class Config(object):
  """Configuration object that Read() returns to the caller."""

  def __init__(self, blacklist_patterns, whitelist_patterns):
    self.blacklist_patterns = blacklist_patterns
    self.whitelist_patterns = whitelist_patterns


def OpenAndRead(relative_path='debugger-blacklist.yaml'):
  """Attempts to find the yaml configuration file, then read it.

  Args:
    relative_path: Optional relative path override.

  Returns:
    A Config object if the open and read were successful, None if the file
    does not exist (which is not considered an error).

  Raises:
    Error (some subclass): As thrown by the called Read() function.
  """

  # Note: This logic follows the convention established by source-context.json
  try:
    with open(os.path.join(sys.path[0], relative_path), 'r') as f:
      return Read(f)
  except IOError:
    return None


def Read(f):
  """Reads and returns Config data from a yaml file.

  Args:
    f: Yaml file to parse.

  Returns:
    Config object as defined in this file.

  Raises:
    Error (some subclass): If there is a problem loading or parsing the file.
  """
  try:
    yaml_data = yaml.safe_load(f)
  except yaml.YAMLError as e:
    raise ParseError('%s' % e)
  except IOError as e:
    raise YAMLLoadError('%s' % e)

  _CheckData(yaml_data)

  try:
    return Config(
        yaml_data.get('blacklist', ()),
        yaml_data.get('whitelist', ('*')))
  except UnicodeDecodeError as e:
    raise YAMLLoadError('%s' % e)


def _CheckData(yaml_data):
  """Checks data for illegal keys and formatting."""
  legal_keys = set(('blacklist', 'whitelist'))
  unknown_keys = set(yaml_data) - legal_keys
  if unknown_keys:
    raise UnknownConfigKeyError(
        'Unknown keys in configuration: %s' % unknown_keys)

  for key, data in yaml_data.items():
    _AssertDataIsList(key, data)


def _AssertDataIsList(key, lst):
  """Assert that lst contains list data and is not structured."""

  # list and tuple are supported.  Not supported are direct strings
  # and dictionary; these indicate too much or two little structure.
  if not isinstance(lst, list) and not isinstance(lst, tuple):
    raise NotAListError('%s must be a list' % key)

  # each list entry must be a string
  for element in lst:
    if not isinstance(element, str):
      raise ElementNotAStringError('Unsupported list element %s found in %s',
                                   (element, lst))
