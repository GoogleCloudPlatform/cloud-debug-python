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

"""Implements exponential backoff for retry timeouts."""


class Backoff(object):
  """Exponential backoff for retry timeouts.

  This class manages delay between retries for a single kind of request. It
  starts from a small delay. The delay is exponentially increased between
  subsequent failures, up to the specified maximum. Once the request succeeds
  once, the delay is reset to minimum.

  Attributes:
    min_interval_sec: initial small delay.
    max_interval_sec: maximum delay between retries.
    multiplier: factor for exponential increase.
  """

  def __init__(self, min_interval_sec=10, max_interval_sec=600, multiplier=2):
    """Class constructor.

    Args:
      min_interval_sec: initial small delay.
      max_interval_sec: maximum delay between retries.
      multiplier: factor for exponential increase.
    """
    self.min_interval_sec = min_interval_sec
    self.max_interval_sec = max_interval_sec
    self.multiplier = multiplier
    self.Succeeded()

  def Succeeded(self):
    """Resets the delay to minimum upon request success."""
    self._current_interval_sec = self.min_interval_sec

  def Failed(self):
    """Indicates that a request has failed.

    Returns:
      Time interval to wait before retrying (in seconds).
    """
    interval = self._current_interval_sec
    self._current_interval_sec = min(
        self.max_interval_sec, self._current_interval_sec * self.multiplier)
    return interval
