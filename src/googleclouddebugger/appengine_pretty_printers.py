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

"""Formatters for well known objects that don't show up nicely by default."""

try:
  from google.appengine.ext import ndb  # pylint: disable=g-import-not-at-top
except ImportError:
  ndb = None


def PrettyPrinter(obj):
  """Pretty printers for AppEngine objects."""

  if ndb and isinstance(obj, ndb.Model):
    return obj.to_dict().iteritems(), 'ndb.Model(%s)' % type(obj).__name__

  return None
