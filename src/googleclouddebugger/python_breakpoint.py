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
import module_explorer
import module_lookup

# TODO(vlif): move to messages.py module.
BREAKPOINT_ONLY_SUPPORTS_PY_FILES = (
    'Only files with .py or .pyc extension are supported')
MODULE_NOT_FOUND = (
    'Python module not found')
NO_CODE_FOUND_AT_LINE = (
    'No code found at line $0')
BREAKPOINTS_EMULATOR_QUOTA_EXCEEDED = (
    'Active snapshots might affect the application performance')
GLOBAL_CONDITION_QUOTA_EXCEEDED = (
    'Snapshot cancelled. The condition evaluation cost for all active '
    'snapshots might affect the application performance.')
BREAKPOINT_CONDITION_QUOTA_EXCEEDED = (
    'Snapshot cancelled. The condition evaluation at this location might '
    'affect application performance. Please simplify the condition or move '
    'the snapshot to a less frequently called statement.')
MUTABLE_CONDITION = (
    'Only immutable expressions can be used in snapshot conditions')
BREAKPOINT_EXPIRED = (
    'The snapshot has expired')

# Status messages for different breakpoint events (except of "hit").
_BREAKPOINT_EVENT_STATUS = dict(
    [(native.BREAKPOINT_EVENT_GLOBAL_CONDITION_QUOTA_EXCEEDED,
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


class PythonBreakpoint(object):
  """Handles a single Python breakpoint.

  Taking care of a breakpoint starts with setting one and evaluating
  condition. When a breakpoint we need to evaluate all the watched expressions
  and take an action. The action can be either to collect all the data or
  to log a statement.
  """

  def __init__(self, definition, hub_client, breakpoints_manager):
    """Class constructor.

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

    # Find the code object in which the breakpoint is being set.
    path = self.definition['location']['path']
    line = self.definition['location']['line']

    code_object = self._FindCodeObject(path, line)
    if not code_object:
      return  # _FindCodeObject already completed the breakpoint with an error.

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
        return
      except SyntaxError as e:
        self._CompleteBreakpoint({
            'status': {
                'isError': True,
                'refersTo': 'BREAKPOINT_CONDITION',
                'description': {
                    'format': 'Expression could not be compiled: $0',
                    'parameters': [e.msg]}}})
        return

    native.LogInfo('Creating new Python breakpoint %s in %s, line %d' % (
        self.GetBreakpointId(), code_object, line))

    self._lock = Lock()
    self._completed = False
    self._cookie = native.SetConditionalBreakpoint(
        code_object,
        line,
        condition,
        self._BreakpointEvent)

  def Clear(self):
    """Clears the breakpoint and releases all breakpoint resources.

    This function is assumed to be called by BreakpointsManager. Therefore we
    don't call CompleteBreakpoint from here.
    """
    if self._cookie is not None:
      native.LogInfo('Clearing breakpoint %s' % self.GetBreakpointId())
      native.ClearConditionalBreakpoint(self._cookie)
      self._cookie = None

  def BreakpointsEmulatorQuotaExceeded(self):
    self._CompleteBreakpoint({
        'status': {
            'isError': True,
            'description': {'format': BREAKPOINTS_EMULATOR_QUOTA_EXCEEDED}}})

  def GetBreakpointId(self):
    return self.definition['id']

  def GetExpirationTime(self):
    """Computes the timestamp at which this breakpoint will expire."""
    create_datetime = datetime.strptime(
        self.definition['createTime'].replace('Z', 'UTC'),
        '%Y-%m-%dT%H:%M:%S.%f%Z')
    return create_datetime + self.expiration_period

  def ExpireBreakpoint(self):
    """Expires this breakpoint."""
    # Let only one thread capture the data and complete the breakpoint.
    if not self._SetCompleted():
      return

    self._CompleteBreakpoint({
        'status': {
            'isError': True,
            'refersTo': 'UNSPECIFIED',
            'description': {'format': BREAKPOINT_EXPIRED}}})

  def _FindCodeObject(self, source_path, line):
    """Finds the target code object for the breakpoint.

    If module is not found or if there is no code at the specified line, this
    function completes the breakpoint with error.

    Args:
      source_path: breakpoint location.
      line: 1-based source line number.

    Returns:
      Python code object object in which the breakpoint will be set or None if
      module not found or if there is no code at the specified line.
    """
    if os.path.splitext(source_path)[1] not in ['.py', '.pyc']:
      self._CompleteBreakpoint({
          'status': {
              'isError': True,
              'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
              'description': {'format': BREAKPOINT_ONLY_SUPPORTS_PY_FILES}}})
      return None

    module = module_lookup.FindModule(source_path)
    if not module:
      self._CompleteBreakpoint({
          'status': {
              'isError': True,
              'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
              'description': {'format': MODULE_NOT_FOUND}}})
      return None

    code_object = module_explorer.GetCodeObjectAtLine(module, line)
    if code_object is None:
      self._CompleteBreakpoint({
          'status': {
              'isError': True,
              'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
              'description': {
                  'format': NO_CODE_FOUND_AT_LINE,
                  'parameters': [str(line)]}}})
      return None

    return code_object

  def _CompleteBreakpoint(self, data, is_incremental=True):
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
    # TODO(vlif): support dynamic log breakpoints.

    # Let only one thread capture the data and complete the breakpoint.
    if not self._SetCompleted():
      return

    self.Clear()

    if event == native.BREAKPOINT_EVENT_HIT:
      collector = capture_collector.CaptureCollector(self.definition)
      collector.Collect(frame)

      self._CompleteBreakpoint(collector.breakpoint, is_incremental=False)
    elif event == native.BREAKPOINT_EVENT_EMULATOR_QUOTA_EXCEEDED:
      self._breakpoints_manager.BreakpointsEmulatorQuotaExceeded()
    else:
      self._CompleteBreakpoint({'status': _BREAKPOINT_EVENT_STATUS[event]})
