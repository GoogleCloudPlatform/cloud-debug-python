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

"""Defines the keys of the well known labels used by the cloud debugger.

DO NOT EDIT - This file is auto-generated
"""


class Debuggee(object):
  MINOR_VERSION = 'minorversion'
  PROJECT_ID = 'projectid'
  MODULE = 'module'
  DOMAIN = 'domain'
  VERSION = 'version'

  SET_ALL = frozenset([
      'minorversion',
      'projectid',
      'module',
      'domain',
      'version',
      ])

class Breakpoint(object):
  REQUEST_LOG_ID = 'requestlogid'

  SET_ALL = frozenset([
      'requestlogid',
      ])

