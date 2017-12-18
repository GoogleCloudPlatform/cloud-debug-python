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

"""Always returns the provided error on visibility requests.

Example Usage:

  policy = ErrorDataVisibilityPolicy('An error message')

  policy.IsDataVisible('org.foo.bar') -> (False, 'An error message')
"""


class ErrorDataVisibilityPolicy(object):
  """Visibility policy that always returns an error to the caller."""

  def __init__(self, error_message):
    self.error_message = error_message

  def IsDataVisible(self, unused_path):
    return (False, self.error_message)
