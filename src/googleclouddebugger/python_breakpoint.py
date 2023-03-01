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

from . import collector
from . import cdbg_native as native
from . import imphook
from . import module_explorer
from . import module_search
from . import module_utils

# TODO: move to messages.py module.
# Use the following schema to define breakpoint error message constant:
# ERROR_<Single word from Status.Reference>_<short error name>_<num params>
ERROR_LOCATION_FILE_EXTENSION_0 = (
    'Only files with .py extension are supported')
ERROR_LOCATION_MODULE_NOT_FOUND_0 = (
    'Python module not found. Please ensure this file is present in the '
    'version of the service you are trying to debug.')
ERROR_LOCATION_MULTIPLE_MODULES_1 = (
    'Multiple modules matching $0. Please specify the module path.')
ERROR_LOCATION_MULTIPLE_MODULES_3 = ('Multiple modules matching $0 ($1, $2)')
ERROR_LOCATION_MULTIPLE_MODULES_4 = (
    'Multiple modules matching $0 ($1, $2, and $3 more)')
ERROR_LOCATION_NO_CODE_FOUND_AT_LINE_2 = 'No code found at line $0 in $1'
ERROR_LOCATION_NO_CODE_FOUND_AT_LINE_3 = (
    'No code found at line $0 in $1. Try line $2.')
ERROR_LOCATION_NO_CODE_FOUND_AT_LINE_4 = (
    'No code found at line $0 in $1. Try lines $2 or $3.')
ERROR_CONDITION_GLOBAL_QUOTA_EXCEEDED_0 = (
    'Snapshot cancelled. The condition evaluation cost for all active '
    'snapshots might affect the application performance.')
ERROR_CONDITION_BREAKPOINT_QUOTA_EXCEEDED_0 = (
    'Snapshot cancelled. The condition evaluation at this location might '
    'affect application performance. Please simplify the condition or move '
    'the snapshot to a less frequently called statement.')
ERROR_CONDITION_MUTABLE_0 = (
    'Only immutable expressions can be used in snapshot conditions')
ERROR_AGE_SNAPSHOT_EXPIRED_0 = ('The snapshot has expired')
ERROR_AGE_LOGPOINT_EXPIRED_0 = ('The logpoint has expired')
ERROR_UNSPECIFIED_INTERNAL_ERROR = ('Internal error occurred')

# Status messages for different breakpoint events (except of "hit").
_BREAKPOINT_EVENT_STATUS = dict([
    (native.BREAKPOINT_EVENT_ERROR, {
        'isError': True,
        'description': {
            'format': ERROR_UNSPECIFIED_INTERNAL_ERROR
        }
    }),
    (native.BREAKPOINT_EVENT_GLOBAL_CONDITION_QUOTA_EXCEEDED, {
        'isError': True,
        'refersTo': 'BREAKPOINT_CONDITION',
        'description': {
            'format': ERROR_CONDITION_GLOBAL_QUOTA_EXCEEDED_0
        }
    }),
    (native.BREAKPOINT_EVENT_BREAKPOINT_CONDITION_QUOTA_EXCEEDED, {
        'isError': True,
        'refersTo': 'BREAKPOINT_CONDITION',
        'description': {
            'format': ERROR_CONDITION_BREAKPOINT_QUOTA_EXCEEDED_0
        }
    }),
    (native.BREAKPOINT_EVENT_CONDITION_EXPRESSION_MUTABLE, {
        'isError': True,
        'refersTo': 'BREAKPOINT_CONDITION',
        'description': {
            'format': ERROR_CONDITION_MUTABLE_0
        }
    })
])

# The implementation of datetime.strptime imports an undocumented module called
# _strptime. If it happens at the wrong time, we can get an exception about
# trying to import while another thread holds the import lock. This dummy call
# to strptime ensures that the module is loaded at startup.
# See http://bugs.python.org/issue7980 for discussion of the Python bug.
datetime.strptime('2017-01-01', '%Y-%m-%d')


def _IsRootInitPy(path):
  return path.lstrip(os.sep) == '__init__.py'


def _StripCommonPathPrefix(paths):
  """Removes path common prefix from a list of path strings."""
  # Find the longest common prefix in terms of characters.
  common_prefix = os.path.commonprefix(paths)
  # Truncate at last segment boundary. E.g. '/aa/bb1/x.py' and '/a/bb2/x.py'
  # have '/aa/bb' as the common prefix, but we should strip '/aa/' instead.
  # If there's no '/' found, returns -1+1=0.
  common_prefix_len = common_prefix.rfind('/') + 1
  return [path[common_prefix_len:] for path in paths]


def _MultipleModulesFoundError(path, candidates):
  """Generates an error message to be used when multiple matches are found.

  Args:
    path: The breakpoint location path that the user provided.
    candidates: List of paths that match the user provided path. Must
        contain at least 2 entries (throws AssertionError otherwise).

  Returns:
    A (format, parameters) tuple that should be used in the description
    field of the breakpoint error status.
  """
  assert len(candidates) > 1
  params = [path] + _StripCommonPathPrefix(candidates[:2])
  if len(candidates) == 2:
    fmt = ERROR_LOCATION_MULTIPLE_MODULES_3
  else:
    fmt = ERROR_LOCATION_MULTIPLE_MODULES_4
    params.append(str(len(candidates) - 2))
  return fmt, params


def _NormalizePath(path):
  """Removes surrounding whitespace, leading separator and normalize."""
  return module_utils.NormalizePath(path.strip().lstrip(os.sep))


class PythonBreakpoint(object):
  """Handles a single Python breakpoint.

  Taking care of a breakpoint starts with setting one and evaluating
  condition. When a breakpoint we need to evaluate all the watched expressions
  and take an action. The action can be either to collect all the data or
  to log a statement.
  """

  def __init__(self, definition, hub_client, breakpoints_manager,
               data_visibility_policy):
    """Class constructor.

    Tries to set the breakpoint. If the source location is invalid, the
    breakpoint is completed with an error message. If the source location is
    valid, but the module hasn't been loaded yet, the breakpoint is deferred.

    Args:
      definition: breakpoint definition as it came from the backend.
      hub_client: asynchronously sends breakpoint updates to the backend.
      breakpoints_manager: parent object managing active breakpoints.
      data_visibility_policy: An object used to determine the visibility
          of a captured variable.  May be None if no policy is available.
    """
    self.definition = definition

    self.data_visibility_policy = data_visibility_policy

    # Breakpoint expiration time.
    self.expiration_period = timedelta(hours=24)
    if self.definition.get('expires_in'):
      self.expiration_period = min(
          timedelta(definition.get('expires_in').get('seconds', 0)),
          self.expiration_period)

    self._hub_client = hub_client
    self._breakpoints_manager = breakpoints_manager
    self._cookie = None
    self._import_hook_cleanup = None

    self._lock = Lock()
    self._completed = False

    if self.definition.get('action') == 'LOG':
      self._collector = collector.LogCollector(self.definition)

    path = _NormalizePath(self.definition['location']['path'])

    # Only accept .py extension.
    if os.path.splitext(path)[1] != '.py':
      self._CompleteBreakpoint({
          'status': {
              'isError': True,
              'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
              'description': {
                  'format': ERROR_LOCATION_FILE_EXTENSION_0
              }
          }
      })
      return

    # A flat init file is too generic; path must include package name.
    if path == '__init__.py':
      self._CompleteBreakpoint({
          'status': {
              'isError': True,
              'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
              'description': {
                  'format': ERROR_LOCATION_MULTIPLE_MODULES_1,
                  'parameters': [path]
              }
          }
      })
      return

    new_path = module_search.Search(path)
    new_module = module_utils.GetLoadedModuleBySuffix(new_path)

    if new_module:
      self._ActivateBreakpoint(new_module)
    else:
      self._import_hook_cleanup = imphook.AddImportCallbackBySuffix(
          new_path, self._ActivateBreakpoint)

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
    """Computes the timestamp at which this breakpoint will expire.

    If no creation time can be found an expiration time in the past will be
    used.
    """
    return self.GetCreateTime() + self.expiration_period

  def GetCreateTime(self):
    """Retrieves the creation time of this breakpoint.

    If no creation time can be found a creation time in the past will be used.
    """
    if 'createTime' in self.definition:
      return self.GetTimeFromRfc3339Str(self.definition['createTime'])
    else:
      return self.GetTimeFromUnixMsec(
          self.definition.get('createTimeUnixMsec', 0))

  def GetTimeFromRfc3339Str(self, rfc3339_str):
    if '.' not in rfc3339_str:
      fmt = '%Y-%m-%dT%H:%M:%S%Z'
    else:
      fmt = '%Y-%m-%dT%H:%M:%S.%f%Z'

    return datetime.strptime(rfc3339_str.replace('Z', 'UTC'), fmt)

  def GetTimeFromUnixMsec(self, unix_msec):
    try:
      return datetime.fromtimestamp(unix_msec / 1000)
    except (TypeError, ValueError, OSError, OverflowError) as e:
      native.LogWarning(
        'Unexpected error (%s) occured processing unix_msec %s, breakpoint: %s'
        % (repr(e), str(unix_msec), self.GetBreakpointId()))
      return datetime.fromtimestamp(0)

  def ExpireBreakpoint(self):
    """Expires this breakpoint."""
    # Let only one thread capture the data and complete the breakpoint.
    if not self._SetCompleted():
      return

    if self.definition.get('action') == 'LOG':
      message = ERROR_AGE_LOGPOINT_EXPIRED_0
    else:
      message = ERROR_AGE_SNAPSHOT_EXPIRED_0
    self._CompleteBreakpoint({
        'status': {
            'isError': True,
            'refersTo': 'BREAKPOINT_AGE',
            'description': {
                'format': message
            }
        }
    })

  def _ActivateBreakpoint(self, module):
    """Sets the breakpoint in the loaded module, or complete with error."""

    # First remove the import hook (if installed).
    self._RemoveImportHook()

    line = self.definition['location']['line']

    # Find the code object in which the breakpoint is being set.
    status, codeobj = module_explorer.GetCodeObjectAtLine(module, line)
    if not status:
      # First two parameters are common: the line of the breakpoint and the
      # module we are trying to insert the breakpoint in.
      # TODO: Do not display the entire path of the file. Either
      # strip some prefix, or display the path in the breakpoint.
      params = [str(line), os.path.splitext(module.__file__)[0] + '.py']

      # The next 0, 1, or 2 parameters are the alternative lines to set the
      # breakpoint at, displayed for the user's convenience.
      alt_lines = (str(l) for l in codeobj if l is not None)
      params += alt_lines

      if len(params) == 4:
        fmt = ERROR_LOCATION_NO_CODE_FOUND_AT_LINE_4
      elif len(params) == 3:
        fmt = ERROR_LOCATION_NO_CODE_FOUND_AT_LINE_3
      else:
        fmt = ERROR_LOCATION_NO_CODE_FOUND_AT_LINE_2

      self._CompleteBreakpoint({
          'status': {
              'isError': True,
              'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
              'description': {
                  'format': fmt,
                  'parameters': params
              }
          }
      })
      return

    # Compile the breakpoint condition.
    condition = None
    if self.definition.get('condition'):
      try:
        condition = compile(
            self.definition.get('condition'), '<condition_expression>', 'eval')
      except (TypeError, ValueError) as e:
        # condition string contains null bytes.
        self._CompleteBreakpoint({
            'status': {
                'isError': True,
                'refersTo': 'BREAKPOINT_CONDITION',
                'description': {
                    'format': 'Invalid expression',
                    'parameters': [str(e)]
                }
            }
        })
        return

      except SyntaxError as e:
        self._CompleteBreakpoint({
            'status': {
                'isError': True,
                'refersTo': 'BREAKPOINT_CONDITION',
                'description': {
                    'format': 'Expression could not be compiled: $0',
                    'parameters': [e.msg]
                }
            }
        })
        return

    native.LogInfo('Creating new Python breakpoint %s in %s, line %d' %
                   (self.GetBreakpointId(), codeobj, line))

    self._cookie = native.CreateConditionalBreakpoint(codeobj, line, condition,
                                                      self._BreakpointEvent)

    native.ActivateConditionalBreakpoint(self._cookie)

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

    capture_collector = collector.CaptureCollector(self.definition,
                                                   self.data_visibility_policy)

    # TODO: This is a temporary try/except. All exceptions should be
    # caught inside Collect and converted into breakpoint error messages.
    try:
      capture_collector.Collect(frame)
    except BaseException as e:  # pylint: disable=broad-except
      native.LogInfo('Internal error during data capture: %s' % repr(e))
      error_status = {
          'isError': True,
          'description': {
              'format': ('Internal error while capturing data: %s' % repr(e))
          }
      }
      self._CompleteBreakpoint({'status': error_status})
      return
    except:  # pylint: disable=bare-except
      native.LogInfo('Unknown exception raised')
      error_status = {
          'isError': True,
          'description': {
              'format': 'Unknown internal error'
          }
      }
      self._CompleteBreakpoint({'status': error_status})
      return

    self._CompleteBreakpoint(capture_collector.breakpoint, is_incremental=False)
