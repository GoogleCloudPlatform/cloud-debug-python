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

"""Handles a single Python breakpoint."""

from datetime import datetime
from datetime import timedelta
import os
from threading import Lock

import capture_collector
import cdbg_native as native
import deferred_modules
import module_explorer
import module_lookup

# TODO(vlif): move to messages.py module.
BREAKPOINT_ONLY_SUPPORTS_PY_FILES = (
    'Only files with .py or .pyc extension are supported')
MODULE_NOT_FOUND = (
    'Python module not found. Please ensure this file is present in the '
    'version of the service you are trying to debug.')
NO_CODE_FOUND_AT_LINE = 'No code found at line $0 in $1'
NO_CODE_FOUND_AT_LINE_ALT_LINE = (
    'No code found at line $0 in $1. Try line $2.')
NO_CODE_FOUND_AT_LINE_TWO_ALT_LINES = (
    'No code found at line $0 in $1. Try lines $2 or $3.')
GLOBAL_CONDITION_QUOTA_EXCEEDED = (
    'Snapshot cancelled. The condition evaluation cost for all active '
    'snapshots might affect the application performance.')
BREAKPOINT_CONDITION_QUOTA_EXCEEDED = (
    'Snapshot cancelled. The condition evaluation at this location might '
    'affect application performance. Please simplify the condition or move '
    'the snapshot to a less frequently called statement.')
MUTABLE_CONDITION = (
    'Only immutable expressions can be used in snapshot conditions')
SNAPSHOT_EXPIRED = (
    'The snapshot has expired')
LOGPOINT_EXPIRED = (
    'The logpoint has expired')
INTERNAL_ERROR = (
    'Internal error occurred')

# Status messages for different breakpoint events (except of "hit").
_BREAKPOINT_EVENT_STATUS = dict(
    [(native.BREAKPOINT_EVENT_ERROR,
      {'isError': True,
       'description': {'format': INTERNAL_ERROR}}),
     (native.BREAKPOINT_EVENT_GLOBAL_CONDITION_QUOTA_EXCEEDED,
      {'isError': True,
       'refersTo': 'BREAKPOINT_CONDITION',
       'description': {'format': GLOBAL_CONDITION_QUOTA_EXCEEDED}}),
     (native.BREAKPOINT_EVENT_BREAKPOINT_CONDITION_QUOTA_EXCEEDED,
      {'isError': True,
       'refersTo': 'BREAKPOINT_CONDITION',
       'description': {'format': BREAKPOINT_CONDITION_QUOTA_EXCEEDED}}),
     (native.BREAKPOINT_EVENT_CONDITION_EXPRESSION_MUTABLE,
      {'isError': True,
       'refersTo': 'BREAKPOINT_CONDITION',
       'description': {'format': MUTABLE_CONDITION}})])

# The implementation of datetime.strptime imports an undocumented module called
# _strptime. If it happens at the wrong time, we can get an exception about
# trying to import while another thread holds the import lock. This dummy call
# to strptime ensures that the module is loaded at startup.
# See http://bugs.python.org/issue7980 for discussion of the Python bug.
datetime.strptime('2017-01-01', '%Y-%m-%d')


class PythonBreakpoint(object):
  """Handles a single Python breakpoint.

  Taking care of a breakpoint starts with setting one and evaluating
  condition. When a breakpoint we need to evaluate all the watched expressions
  and take an action. The action can be either to collect all the data or
  to log a statement.
  """

  def __init__(self, definition, hub_client, breakpoints_manager):
    """Class constructor.

    Tries to set the breakpoint. If the source location is invalid, the
    breakpoint is completed with an error message. If the source location is
    valid, but the module hasn't been loaded yet, the breakpoint is initialized
    as deferred.

    Args:
      definition: breakpoint definition as it came from the backend.
      hub_client: asynchronously sends breakpoint updates to the backend.
      breakpoints_manager: parent object managing active breakpoints.
    """
    self.definition = definition

    # Breakpoint expiration time.
    self.expiration_period = timedelta(hours=24)

    self._hub_client = hub_client
    self._breakpoints_manager = breakpoints_manager
    self._cookie = None
    self._import_hook_cleanup = None

    self._lock = Lock()
    self._completed = False

    if self.definition.get('action') == 'LOG':
      self._collector = capture_collector.LogCollector(self.definition)

    if not self._TryActivateBreakpoint() and not self._completed:
      self._DeferBreakpoint()

  def Clear(self):
    """Clears the breakpoint and releases all breakpoint resources.

    This function is assumed to be called by BreakpointsManager. Therefore we
    don't call CompleteBreakpoint from here.
    """
    self._RemoveImportHook()
    if self._cookie is not None:
      native.LogInfo('Clearing breakpoint %s' % self.GetBreakpointId())
      native.ClearConditionalBreakpoint(self._cookie)
      self._cookie = None

    self._completed = True  # Never again send updates for this breakpoint.

  def GetBreakpointId(self):
    return self.definition['id']

  def GetExpirationTime(self):
    """Computes the timestamp at which this breakpoint will expire."""
    # TODO(emrekultursay): Move this to a common method.
    if '.' not in self.definition['createTime']:
      fmt = '%Y-%m-%dT%H:%M:%S%Z'
    else:
      fmt = '%Y-%m-%dT%H:%M:%S.%f%Z'

    create_datetime = datetime.strptime(
        self.definition['createTime'].replace('Z', 'UTC'), fmt)
    return create_datetime + self.expiration_period

  def ExpireBreakpoint(self):
    """Expires this breakpoint."""
    # Let only one thread capture the data and complete the breakpoint.
    if not self._SetCompleted():
      return

    if self.definition.get('action') == 'LOG':
      message = LOGPOINT_EXPIRED
    else:
      message = SNAPSHOT_EXPIRED
    self._CompleteBreakpoint({
        'status': {
            'isError': True,
            'refersTo': 'BREAKPOINT_AGE',
            'description': {'format': message}}})

  def _TryActivateBreakpoint(self):
    """Sets the breakpoint if the module has already been loaded.

    This function will complete the breakpoint with error if breakpoint
    definition is incorrect. Examples: invalid line or bad condition.

    If the code object corresponding to the source path can't be found,
    this function returns False. In this case, the breakpoint is not
    completed, since the breakpoint may be deferred.

    Returns:
      True if breakpoint was set or false otherwise. False can be returned
      for potentially deferred breakpoints or in case of a bad breakpoint
      definition. The self._completed flag distinguishes between the two cases.
    """

    # Find the code object in which the breakpoint is being set.
    code_object = self._FindCodeObject()
    if not code_object:
      return False

    # Compile the breakpoint condition.
    condition = None
    if self.definition.get('condition'):
      try:
        condition = compile(self.definition.get('condition'),
                            '<condition_expression>',
                            'eval')
      except TypeError as e:  # condition string contains null bytes.
        self._CompleteBreakpoint({
            'status': {
                'isError': True,
                'refersTo': 'BREAKPOINT_CONDITION',
                'description': {
                    'format': 'Invalid expression',
                    'parameters': [str(e)]}}})
        return False
      except SyntaxError as e:
        self._CompleteBreakpoint({
            'status': {
                'isError': True,
                'refersTo': 'BREAKPOINT_CONDITION',
                'description': {
                    'format': 'Expression could not be compiled: $0',
                    'parameters': [e.msg]}}})
        return False

    line = self.definition['location']['line']

    native.LogInfo('Creating new Python breakpoint %s in %s, line %d' % (
        self.GetBreakpointId(), code_object, line))

    self._cookie = native.SetConditionalBreakpoint(
        code_object,
        line,
        condition,
        self._BreakpointEvent)

    self._RemoveImportHook()
    return True

  def _FindCodeObject(self):
    """Finds the target code object for the breakpoint.

    This function completes breakpoint with error if the module was found,
    but the line number is invalid. When code object is not found for the
    breakpoint source location, this function just returns None. It does not
    assume error, because it might be a deferred breakpoint.

    Returns:
      Python code object object in which the breakpoint will be set or None if
      module not found or if there is no code at the specified line.
    """
    path = self.definition['location']['path']
    line = self.definition['location']['line']

    module = module_lookup.FindModule(path)
    if not module:
      return None

    status, val = module_explorer.GetCodeObjectAtLine(module, line)
    if not status:
      # module.__file__ must be defined or else it wouldn't have been returned
      # from FindModule
      params = [str(line), module.__file__]
      alt_lines = (str(l) for l in val if l is not None)
      params += alt_lines

      if len(params) == 4:
        fmt = NO_CODE_FOUND_AT_LINE_TWO_ALT_LINES
      elif len(params) == 3:
        fmt = NO_CODE_FOUND_AT_LINE_ALT_LINE
      else:
        fmt = NO_CODE_FOUND_AT_LINE

      self._CompleteBreakpoint({
          'status': {
              'isError': True,
              'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
              'description': {
                  'format': fmt,
                  'parameters': params}}})
      return None

    return val

  # Enables deferred breakpoints.
  def _DeferBreakpoint(self):
    """Defers breakpoint activation until the module has been loaded.

    This function first verifies that a module corresponding to breakpoint
    location exists. This way if the user sets breakpoint in a file that
    doesn't even exist, the debugger will not be waiting forever. If there
    is definitely no module that matches this breakpoint, this function
    completes the breakpoint with error status.

    Otherwise the debugger assumes that the module corresponding to breakpoint
    location hasn't been loaded yet. The debugger will then start waiting for
    the module to get loaded. Once the module is loaded, the debugger
    will automatically try to activate the breakpoint.
    """
    path = self.definition['location']['path']

    if os.path.splitext(path)[1] != '.py':
      self._CompleteBreakpoint({
          'status': {
              'isError': True,
              'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
              'description': {'format': BREAKPOINT_ONLY_SUPPORTS_PY_FILES}}})
      return

    if not deferred_modules.IsValidSourcePath(path):
      self._CompleteBreakpoint({
          'status': {
              'isError': True,
              'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
              'description': {'format': MODULE_NOT_FOUND}}})

    assert not self._import_hook_cleanup
    self._import_hook_cleanup = deferred_modules.AddImportCallback(
        self.definition['location']['path'],
        lambda unused_module_name: self._TryActivateBreakpoint())

  def _RemoveImportHook(self):
    """Removes the import hook if one was installed."""
    if self._import_hook_cleanup:
      self._import_hook_cleanup()
      self._import_hook_cleanup = None

  def _CompleteBreakpoint(self, data, is_incremental=True):
    """Sends breakpoint update and deactivates the breakpoint."""
    if is_incremental:
      data = dict(self.definition, **data)
    data['isFinalState'] = True

    self._hub_client.EnqueueBreakpointUpdate(data)
    self._breakpoints_manager.CompleteBreakpoint(self.GetBreakpointId())
    self.Clear()

  def _SetCompleted(self):
    """Atomically marks the breakpoint as completed.

    Returns:
      True if the breakpoint wasn't marked already completed or False if the
      breakpoint was already completed.
    """
    with self._lock:
      if self._completed:
        return False
      self._completed = True
      return True

  def _BreakpointEvent(self, event, frame):
    """Callback invoked by cdbg_native when breakpoint hits.

    Args:
      event: breakpoint event (see kIntegerConstants in native_module.cc).
      frame: Python stack frame of breakpoint hit or None for other events.
    """
    error_status = None

    if event != native.BREAKPOINT_EVENT_HIT:
      error_status = _BREAKPOINT_EVENT_STATUS[event]
    elif self.definition.get('action') == 'LOG':
      error_status = self._collector.Log(frame)
      if not error_status:
        return  # Log action successful, no need to clear the breakpoint.

    # Let only one thread capture the data and complete the breakpoint.
    if not self._SetCompleted():
      return

    self.Clear()

    if error_status:
      self._CompleteBreakpoint({'status': error_status})
      return

    collector = capture_collector.CaptureCollector(self.definition)
    collector.Collect(frame)

    self._CompleteBreakpoint(collector.breakpoint, is_incremental=False)
