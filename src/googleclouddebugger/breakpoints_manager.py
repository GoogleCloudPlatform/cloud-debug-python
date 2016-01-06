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

"""Manages lifetime of individual breakpoint objects."""

from datetime import datetime
from threading import RLock

import python_breakpoint


class BreakpointsManager(object):
  """Manages active breakpoints.

  The primary input to this class is the callback indicating that a list of
  active breakpoints has changed. BreakpointsManager compares it with the
  current list of breakpoints. It then creates PythonBreakpoint objects
  corresponding to new breakpoints and removes breakpoints that are no
  longer active.

  This class is thread safe.

  Args:
    hub_client: queries active breakpoints from the backend and sends
        breakpoint updates back to the backend.
  """

  def __init__(self, hub_client):
    self._hub_client = hub_client

    # Lock to synchronize access to data across multiple threads.
    self._lock = RLock()

    # After the breakpoint completes, it is removed from list of active
    # breakpoints. However it takes time until the backend is notified. During
    # this time, the backend will still report the just completed breakpoint
    # as active. We don't want to set the breakpoint again, so we keep a set
    # of completed breakpoint IDs.
    self._completed = set()

    # Map of active breakpoints. The key is breakpoint ID.
    self._active = {}

    # Closest expiration of all active breakpoints or past time if not known.
    self._next_expiration = datetime.max

  def SetActiveBreakpoints(self, breakpoints_data):
    """Adds new breakpoints and removes missing ones.

    Args:
      breakpoints_data: updated list of active breakpoints.
    """
    with self._lock:
      ids = set([x['id'] for x in breakpoints_data])

      # Clear breakpoints that no longer show up in active breakpoints list.
      for breakpoint_id in self._active.viewkeys() - ids:
        self._active.pop(breakpoint_id).Clear()

      # Create new breakpoints.
      self._active.update([
          (x['id'],
           python_breakpoint.PythonBreakpoint(x, self._hub_client, self))
          for x in breakpoints_data
          if x['id'] in ids - self._active.viewkeys() - self._completed])

      # Remove entries from completed_breakpoints_ that weren't listed in
      # breakpoints_data vector. These are confirmed to have been removed by the
      # hub and the debuglet can now assume that they will never show up ever
      # again. The backend never reuses breakpoint IDs.
      self._completed &= ids

      if self._active:
        self._next_expiration = datetime.min  # Not known.
      else:
        self._next_expiration = datetime.max  # Nothing to expire.

  def CompleteBreakpoint(self, breakpoint_id):
    """Marks the specified breaking as completed.

    Appends the ID to set of completed breakpoints and clears it.

    Args:
      breakpoint_id: breakpoint ID to complete.
    """
    with self._lock:
      self._completed.add(breakpoint_id)
      if breakpoint_id in self._active:
        self._active.pop(breakpoint_id).Clear()

  def CheckBreakpointsExpiration(self):
    """Completes all breakpoints that have been active for too long."""
    with self._lock:
      current_time = BreakpointsManager._GetCurrentTime()
      if self._next_expiration > current_time:
        return

      expired_breakpoints = []
      self._next_expiration = datetime.max
      for breakpoint in self._active.itervalues():
        expiration_time = breakpoint.GetExpirationTime()
        if expiration_time <= current_time:
          expired_breakpoints.append(breakpoint)
        else:
          self._next_expiration = min(self._next_expiration, expiration_time)

    for breakpoint in expired_breakpoints:
      breakpoint.ExpireBreakpoint()

  @staticmethod
  def _GetCurrentTime():
    """Wrapper around datetime.now() function.

    The datetime class is a built-in one and therefore not patchable by unit
    tests. We wrap datetime.now() in a static method to work around it.

    Returns:
      Current time
    """
    return datetime.utcnow()
