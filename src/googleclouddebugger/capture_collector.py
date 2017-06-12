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

"""Captures application state on a breakpoint hit."""

# TODO(vlif): rename this file to collector.py.

import copy
import datetime
import inspect
import logging
import os
import re
import sys
import time
import types

import labels
import cdbg_native as native

# Externally defined functions to actually log a message. If these variables
# are not initialized, the log action for breakpoints is invalid.
log_info_message = None
log_warning_message = None
log_error_message = None

# Externally defined function to collect the request log id.
request_log_id_collector = None

_PRIMITIVE_TYPES = (int, long, float, complex, types.StringTypes, bool,
                    types.NoneType, types.SliceType, bytearray)
_DATE_TYPES = (datetime.date, datetime.time, datetime.timedelta)
_VECTOR_TYPES = (types.TupleType, types.ListType, set)

# TODO(vlif): move to messages.py module.
EMPTY_DICTIONARY = 'Empty dictionary'
EMPTY_COLLECTION = 'Empty collection'
OBJECT_HAS_NO_FIELDS = 'Object has no fields'
LOG_ACTION_NOT_SUPPORTED = 'Log action on a breakpoint not supported'
INVALID_EXPRESSION_INDEX = '<N/A>'
DYNAMIC_LOG_OUT_OF_QUOTA = (
    'LOGPOINT: Logpoint is paused due to high log rate until log '
    'quota is restored')


def _ListTypeFormatString(value):
  """Returns the appropriate format string for formatting a list object."""

  if isinstance(value, types.TupleType):
    return '({0})'
  if isinstance(value, set):
    return '{{{0}}}'
  return '[{0}]'


def NormalizePath(path):
  """Removes any Python system path prefix from the given path.

  Python keeps almost all paths absolute. This is not what we actually
  want to return. This loops through system paths (directories in which
  Python will load modules). If "path" is relative to one of them, the
  directory prefix is removed.

  Args:
    path: absolute path to normalize (relative paths will not be altered)

  Returns:
    Relative path if "path" is within one of the sys.path directories or
    the input otherwise.
  """
  path = os.path.normpath(path)

  for sys_path in sys.path:
    if not sys_path:
      continue

    # Append '/' at the end of the path if it's not there already.
    sys_path = os.path.join(sys_path, '')

    if path.startswith(sys_path):
      return path[len(sys_path):]

  return path


class LineNoFilter(logging.Filter):
  """Enables overriding the path and line number in a logging record.

  The "extra" parameter in logging cannot override existing fields in log
  record, so we can't use it to directly set pathname and lineno. Instead,
  we add this filter to the default logger, and it looks for "cdbg_pathname"
  and "cdbg_lineno", moving them to the pathname and lineno fields accordingly.
  """

  def filter(self, record):
    # This method gets invoked for user-generated logging, so verify that this
    # particular invocation came from our logging code.
    if record.pathname != inspect.currentframe().f_code.co_filename:
      return True
    pathname, lineno, func_name = GetLoggingLocation()
    if pathname:
      record.pathname = pathname
      record.filename = os.path.basename(pathname)
      record.lineno = lineno
      record.funcName = func_name
    return True


def GetLoggingLocation():
  """Search for and return the file and line number from the log collector.

  Returns:
    (pathname, lineno, func_name) The full path, line number, and function name
    for the logpoint location.
  """
  frame = inspect.currentframe()
  this_file = frame.f_code.co_filename
  frame = frame.f_back
  while frame:
    if this_file == frame.f_code.co_filename:
      if 'cdbg_logging_location' in frame.f_locals:
        ret = frame.f_locals['cdbg_logging_location']
        if len(ret) != 3:
          return (None, None, None)
        return ret
    frame = frame.f_back
  return (None, None, None)


def SetLogger(logger):
  """Sets the logger object to use for all 'LOG' breakpoint actions."""
  global log_info_message
  global log_warning_message
  global log_error_message
  log_info_message = logger.info
  log_warning_message = logger.warning
  log_error_message = logger.error
  logger.addFilter(LineNoFilter())


class _CaptureLimits(object):
  """Limits for variable capture.

  Args:
    max_value_len: Maximum number of character to allow for a single string
      value.  Longer strings are truncated.
    max_list_items: Maximum number of items in a list to capture.
    max_depth: Maximum depth of dictionaries to capture.
  """

  def __init__(self, max_value_len=256, max_list_items=25, max_depth=5):
    self.max_value_len = max_value_len
    self.max_list_items = max_list_items
    self.max_depth = max_depth


class CaptureCollector(object):
  """Captures application state snapshot.

  Captures call stack, local variables and referenced objects. Then formats the
  result to be sent back to the user.

  The performance of this class is important. Once the breakpoint hits, the
  completion of the user request will be delayed until the collection is over.
  It might make sense to implement this logic in C++.

  Attributes:
    breakpoint: breakpoint definition augmented with captured call stack,
        local variables, arguments and referenced objects.
  """

  # Additional type-specific printers. Each pretty printer is a callable
  # that returns None if it doesn't recognize the object or returns a tuple
  # with iterable enumerating object fields (name-value tuple) and object type
  # string.
  pretty_printers = []

  def __init__(self, definition):
    """Class constructor.

    Args:
      definition: breakpoint definition that this class will augment with
          captured data.
    """
    self.breakpoint = copy.deepcopy(definition)

    self.breakpoint['stackFrames'] = []
    self.breakpoint['evaluatedExpressions'] = []
    self.breakpoint['variableTable'] = [{
        'status': {
            'isError': True,
            'refersTo': 'VARIABLE_VALUE',
            'description': {
                'format': 'Buffer full. Use an expression to see more data'
            }
        }
    }]

    # Shortcut to variables table in the breakpoint message.
    self._var_table = self.breakpoint['variableTable']

    # Maps object ID to its index in variables table.
    self._var_table_index = {}

    # Total size of data collected so far. Limited by max_size.
    self._total_size = 0

    # Maximum number of stack frame to capture. The limit is aimed to reduce
    # the overall collection time.
    self.max_frames = 20

    # Only collect locals and arguments on the few top frames. For the rest of
    # the frames we only collect the source location.
    self.max_expand_frames = 5

    # Maximum amount of data to capture. The application will usually have a
    # lot of objects and we need to stop somewhere to keep the delay
    # reasonable.
    # This constant only counts the collected payload. Overhead due to key
    # names is not counted.
    self.max_size = 32768  # 32 KB

    self.default_capture_limits = _CaptureLimits()

    # When the user provides an expression, they've indicated that they're
    # interested in some specific data. Use higher per-object capture limits
    # for expressions. We don't want to globally increase capture limits,
    # because in the case where the user has not indicated a preference, we
    # don't want a single large object on the stack to use the entire max_size
    # quota and hide the rest of the data.
    self.expression_capture_limits = _CaptureLimits(max_value_len=32768,
                                                    max_list_items=32768)

  def Collect(self, top_frame):
    """Collects call stack, local variables and objects.

    Starts collection from the specified frame. We don't start from the top
    frame to exclude the frames due to debugger. Updates the content of
    self.breakpoint.

    Args:
      top_frame: top frame to start data collection.
    """
    # Evaluate call stack.
    frame = top_frame
    top_line = self.breakpoint['location']['line']
    breakpoint_frames = self.breakpoint['stackFrames']
    # Number of entries in _var_table. Starts at 1 (index 0 is the 'buffer full'
    # status value).
    num_vars = 1
    try:
      # Evaluate watched expressions.
      if 'expressions' in self.breakpoint:
        self.breakpoint['evaluatedExpressions'] = [
            self._CaptureExpression(top_frame, expression) for expression
            in self.breakpoint['expressions']]

      while frame and (len(breakpoint_frames) < self.max_frames):
        line = top_line if frame == top_frame else frame.f_lineno
        code = frame.f_code
        if len(breakpoint_frames) < self.max_expand_frames:
          frame_arguments, frame_locals = self.CaptureFrameLocals(frame)
        else:
          frame_arguments = []
          frame_locals = []

        breakpoint_frames.append({
            'function': _GetFrameCodeObjectName(frame),
            'location': {
                'path': NormalizePath(code.co_filename),
                'line': line
            },
            'arguments': frame_arguments,
            'locals': frame_locals
        })
        frame = frame.f_back

      # Explore variables table in BFS fashion. The variables table will grow
      # inside CaptureVariable as we encounter new references.
      while (num_vars < len(self._var_table)) and (
          self._total_size < self.max_size):
        try:
          self._var_table[num_vars] = self.CaptureVariable(
              self._var_table[num_vars], 0, self.default_capture_limits,
              can_enqueue=False)
          num_vars += 1
        except RuntimeError as e:
          # Capture details on the failure and let the outer handler convert it
          # to a status.
          raise RuntimeError(
              'Failed while capturing an object of type {0}: {1}'.format(
                  type(self._var_table[num_vars]), e))

    except BaseException as e:  # pylint: disable=broad-except
      # The variable table will get serialized even though there was a
      # failure. The results can be useful for diagnosing the internal
      # error so just trim the excess values.
      self.breakpoint['status'] = {
          'isError': True,
          'description': {
              'format': (
                  'INTERNAL ERROR: Debugger failed to capture frame $0: $1'),
              'parameters': [str(len(breakpoint_frames)), str(e)]}}

    # Trim variables table and change make all references to variables that
    # didn't make it point to var_index of 0 ("buffer full")
    self.TrimVariableTable(num_vars)

    self._CaptureRequestLogId()

  def CaptureFrameLocals(self, frame):
    """Captures local variables and arguments of the specified frame.

    Args:
      frame: frame to capture locals and arguments.

    Returns:
      (arguments, locals) tuple.
    """
    # Capture all local variables (including method arguments).
    variables = {n: self.CaptureNamedVariable(n, v, 1,
                                              self.default_capture_limits)
                 for n, v in frame.f_locals.viewitems()}

    # Split between locals and arguments (keeping arguments in the right order).
    nargs = frame.f_code.co_argcount
    if frame.f_code.co_flags & inspect.CO_VARARGS: nargs += 1
    if frame.f_code.co_flags & inspect.CO_VARKEYWORDS: nargs += 1

    frame_arguments = []
    for argname in frame.f_code.co_varnames[:nargs]:
      if argname in variables: frame_arguments.append(variables.pop(argname))

    return (frame_arguments, list(variables.viewvalues()))

  def CaptureNamedVariable(self, name, value, depth, limits):
    """Appends name to the product of CaptureVariable.

    Args:
      name: name of the variable.
      value: data to capture
      depth: nested depth of dictionaries and vectors so far.
      limits: Per-object limits for capturing variable data.

    Returns:
      Formatted captured data as per Variable proto with name.
    """
    try:
      if not hasattr(name, '__dict__'):
        name = str(name)
      else:  # TODO(vlif): call str(name) with immutability verifier here.
        name = str(id(name))
      self._total_size += len(name)

      v = self.CaptureVariable(value, depth, limits)
      v['name'] = name
    except RuntimeError as e:
      raise RuntimeError(
          'INTERNAL ERROR while capturing {0}: {1}'.format(name, e))
    return v

  def CaptureVariablesList(self, items, depth, empty_message, limits):
    """Captures list of named items.

    Args:
      items: iterable of (name, value) tuples.
      depth: nested depth of dictionaries and vectors for items.
      empty_message: info status message to set if items is empty.
      limits: Per-object limits for capturing variable data.

    Returns:
      List of formatted variable objects.
    """
    v = []
    try:
      for name, value in items:
        if (self._total_size >= self.max_size) or (
            len(v) >= limits.max_list_items):
          v.append({
              'status': {
                  'refers_to': 'VARIABLE_VALUE',
                  'description': {
                      'format':
                          ('Only first $0 items were captured. Use in an '
                           'expression to see all items.'),
                      'parameters': [str(len(v))]}}})
          break
        v.append(self.CaptureNamedVariable(name, value, depth, limits))

      if not v:
        return [{'status': {
            'is_error': False,
            'refers_to': 'VARIABLE_NAME',
            'description': {'format': empty_message}}}]
    except RuntimeError as e:
      raise RuntimeError(
          'Failed while capturing variables: {0}\n'
          'The following elements were successfully captured: {1}'.format(
              e, ', '.join([c['name'] for c in v if 'name' in c])))
    return v

  def CaptureVariable(self, value, depth, limits, can_enqueue=True):
    """Captures a single nameless object into Variable message.

    TODO(vlif): safely evaluate iterable types.
    TODO(vlif): safely call str(value)

    Args:
      value: data to capture
      depth: nested depth of dictionaries and vectors so far.
      limits: Per-object limits for capturing variable data.
      can_enqueue: allows referencing the object in variables table.

    Returns:
      Formatted captured data as per Variable proto.
    """
    if depth == limits.max_depth:
      return {'varTableIndex': 0}  # Buffer full.

    if value is None:
      self._total_size += 4
      return {'value': 'None'}

    if isinstance(value, _PRIMITIVE_TYPES):
      r = _TrimString(repr(value),  # Primitive type, always immutable.
                      min(limits.max_value_len,
                          self.max_size - self._total_size))
      self._total_size += len(r)
      return {'value': r, 'type': type(value).__name__}

    if isinstance(value, _DATE_TYPES):
      r = str(value)  # Safe to call str().
      self._total_size += len(r)
      return {'value': r, 'type': 'datetime.'+ type(value).__name__}

    if isinstance(value, dict):
      # Do not use iteritems() here. If GC happens during iteration (which it
      # often can for dictionaries containing large variables), you will get a
      # RunTimeError exception.
      items = [(repr(k), v) for (k, v) in value.items()]
      return {'members':
              self.CaptureVariablesList(items, depth + 1,
                                        EMPTY_DICTIONARY, limits),
              'type': 'dict'}

    if isinstance(value, _VECTOR_TYPES):
      fields = self.CaptureVariablesList(
          (('[%d]' % i, x) for i, x in enumerate(value)),
          depth + 1, EMPTY_COLLECTION, limits)
      return {'members': fields, 'type': type(value).__name__}

    if isinstance(value, types.FunctionType):
      self._total_size += len(value.func_name)
      # TODO(vlif): set value to func_name and type to 'function'
      return {'value': 'function ' + value.func_name}

    if can_enqueue:
      index = self._var_table_index.get(id(value))
      if index is None:
        index = len(self._var_table)
        self._var_table_index[id(value)] = index
        self._var_table.append(value)
      self._total_size += 4  # number of characters to accomodate a number.
      return {'varTableIndex': index}

    for pretty_printer in CaptureCollector.pretty_printers:
      pretty_value = pretty_printer(value)
      if not pretty_value:
        continue

      fields, object_type = pretty_value
      return {'members':
              self.CaptureVariablesList(fields, depth + 1, OBJECT_HAS_NO_FIELDS,
                                        limits),
              'type': object_type}

    if not hasattr(value, '__dict__'):
      # TODO(vlif): keep "value" empty and populate the "type" field instead.
      r = str(type(value))
      self._total_size += len(r)
      return {'value': r}

    # Add an additional depth for the object itself
    members = self.CaptureVariablesList(value.__dict__.items(), depth + 2,
                                        OBJECT_HAS_NO_FIELDS, limits)
    v = {'members': members}

    object_type = type(value)
    if hasattr(object_type, '__name__'):
      type_string = getattr(object_type, '__module__', '')
      if type_string:
        type_string += '.'
      type_string += object_type.__name__
      v['type'] = type_string

    return v

  def _CaptureExpression(self, frame, expression):
    """Evalutes the expression and captures it into a Variable object.

    Args:
      frame: evaluation context.
      expression: watched expression to compile and evaluate.

    Returns:
      Variable object (which will have error status if the expression fails
      to evaluate).
    """
    rc, value = _EvaluateExpression(frame, expression)
    if not rc:
      return {'name': expression, 'status': value}

    return self.CaptureNamedVariable(expression, value, 0,
                                     self.expression_capture_limits)

  def TrimVariableTable(self, new_size):
    """Trims the variable table in the formatted breakpoint message.

    Removes trailing entries in variables table. Then scans the entire
    breakpoint message and replaces references to the trimmed variables to
    point to var_index of 0 ("buffer full").

    Args:
      new_size: desired size of variables table.
    """

    def ProcessBufferFull(variables):
      for variable in variables:
        var_index = variable.get('varTableIndex')
        if var_index is not None and (var_index >= new_size):
          variable['varTableIndex'] = 0  # Buffer full.
        members = variable.get('members')
        if members is not None:
          ProcessBufferFull(members)

    del self._var_table[new_size:]
    ProcessBufferFull(self.breakpoint['evaluatedExpressions'])
    for stack_frame in self.breakpoint['stackFrames']:
      ProcessBufferFull(stack_frame['arguments'])
      ProcessBufferFull(stack_frame['locals'])
    ProcessBufferFull(self._var_table)

  def _CaptureRequestLogId(self):
    """Captures the request log id if possible.

    The request log id is stored inside the breakpoint labels.
    """
    # pylint: disable=not-callable
    if callable(request_log_id_collector):
      request_log_id = request_log_id_collector()
      if request_log_id:
        # We have a request_log_id, save it into the breakpoint labels
        if 'labels' not in self.breakpoint:
          self.breakpoint['labels'] = {}

        self.breakpoint['labels'][
            labels.Breakpoint.REQUEST_LOG_ID] = request_log_id


class LogCollector(object):
  """Captures minimal application snapshot and logs it to application log.

  This is similar to CaptureCollector, but we don't need to capture local
  variables, arguments and the objects tree. All we need to do is to format a
  log message. We still need to evaluate watched expressions.

  The actual log functions are defined globally outside of this module.
  """

  def __init__(self, definition):
    """Class constructor.

    Args:
      definition: breakpoint definition indicating log level, message, etc.
    """
    self._definition = definition

    # Maximum number of character to allow for a single value. Longer strings
    # are truncated.
    self.max_value_len = 256

    # Maximum recursion depth.
    self.max_depth = 2

    # Maximum number of items in a list to capture at the top level.
    self.max_list_items = 10

    # When capturing recursively, limit on the size of sublists.
    self.max_sublist_items = 5

    # Time to pause after dynamic log quota has run out.
    self.quota_recovery_ms = 500

    # The time when we first entered the quota period
    self._quota_recovery_start_time = None

    # Select log function.
    level = self._definition.get('logLevel')
    if not level or level == 'INFO':
      self._log_message = log_info_message
    elif level == 'WARNING':
      self._log_message = log_warning_message
    elif level == 'ERROR':
      self._log_message = log_error_message
    else:
      self._log_message = None

  def Log(self, frame):
    """Captures the minimal application states, formats it and logs the message.

    Args:
      frame: Python stack frame of breakpoint hit.

    Returns:
      None on success or status message on error.
    """
    # Return error if log methods were not configured globally.
    if not self._log_message:
      return {'isError': True,
              'description': {'format': LOG_ACTION_NOT_SUPPORTED}}

    if self._quota_recovery_start_time:
      ms_elapsed = (time.time() - self._quota_recovery_start_time) * 1000
      if ms_elapsed > self.quota_recovery_ms:
        # We are out of the recovery period, clear the time and continue
        self._quota_recovery_start_time = None
      else:
        # We are in the recovery period, exit
        return

    # Evaluate watched expressions.
    message = 'LOGPOINT: ' + _FormatMessage(
        self._definition.get('logMessageFormat', ''),
        self._EvaluateExpressions(frame))

    line = self._definition['location']['line']
    cdbg_logging_location = (NormalizePath(frame.f_code.co_filename), line,
                             _GetFrameCodeObjectName(frame))

    if native.ApplyDynamicLogsQuota(len(message)):
      self._log_message(message)
    else:
      self._quota_recovery_start_time = time.time()
      self._log_message(DYNAMIC_LOG_OUT_OF_QUOTA)
    del cdbg_logging_location
    return None

  def _EvaluateExpressions(self, frame):
    """Evaluates watched expressions into a string form.

    If expression evaluation fails, the error message is used as evaluated
    expression string.

    Args:
      frame: Python stack frame of breakpoint hit.

    Returns:
      Array of strings where each string corresponds to the breakpoint
      expression with the same index.
    """
    return [self._FormatExpression(frame, expression) for expression in
            self._definition.get('expressions') or []]

  def _FormatExpression(self, frame, expression):
    """Evaluates a single watched expression and formats it into a string form.

    If expression evaluation fails, returns error message string.

    Args:
      frame: Python stack frame in which the expression is evaluated.
      expression: string expression to evaluate.

    Returns:
      Formatted expression value that can be used in the log message.
    """
    rc, value = _EvaluateExpression(frame, expression)
    if not rc:
      message = _FormatMessage(value['description']['format'],
                               value['description'].get('parameters'))
      return '<' + message + '>'

    return self._FormatValue(value)

  def _FormatValue(self, value, level=0):
    """Pretty-prints an object for a logger.

    This function is very similar to the standard pprint. The main difference
    is that it enforces limits to make sure we never produce an extremely long
    string or take too much time.

    Args:
      value: Python object to print.
      level: current recursion level.

    Returns:
      Formatted string.
    """

    def FormatDictItem(key_value):
      """Formats single dictionary item."""
      key, value = key_value
      return (self._FormatValue(key, level + 1) +
              ': ' +
              self._FormatValue(value, level + 1))

    def LimitedEnumerate(items, formatter, level=0):
      """Returns items in the specified enumerable enforcing threshold."""
      count = 0
      limit = self.max_sublist_items if level > 0 else self.max_list_items
      for item in items:
        if count == limit:
          yield '...'
          break

        yield formatter(item)
        count += 1

    def FormatList(items, formatter, level=0):
      """Formats a list using a custom item formatter enforcing threshold."""
      return ', '.join(LimitedEnumerate(items, formatter, level=level))

    if isinstance(value, _PRIMITIVE_TYPES):
      return _TrimString(repr(value),  # Primitive type, always immutable.
                         self.max_value_len)

    if isinstance(value, _DATE_TYPES):
      return str(value)

    if level > self.max_depth:
      return str(type(value))

    if isinstance(value, dict):
      return '{' + FormatList(value.iteritems(), FormatDictItem) + '}'

    if isinstance(value, _VECTOR_TYPES):
      return _ListTypeFormatString(value).format(FormatList(
          value, lambda item: self._FormatValue(item, level + 1), level=level))

    if isinstance(value, types.FunctionType):
      return 'function ' + value.func_name

    if hasattr(value, '__dict__') and value.__dict__:
      return self._FormatValue(value.__dict__, level)

    return str(type(value))


def _EvaluateExpression(frame, expression):
  """Compiles and evaluates watched expression.

  Args:
    frame: evaluation context.
    expression: watched expression to compile and evaluate.

  Returns:
    (False, status) on error or (True, value) on success.
  """
  try:
    code = compile(expression, '<watched_expression>', 'eval')
  except TypeError as e:  # condition string contains null bytes.
    return (False, {
        'isError': True,
        'refersTo': 'VARIABLE_NAME',
        'description': {
            'format': 'Invalid expression',
            'parameters': [str(e)]}})
  except SyntaxError as e:
    return (False, {
        'isError': True,
        'refersTo': 'VARIABLE_NAME',
        'description': {
            'format': 'Expression could not be compiled: $0',
            'parameters': [e.msg]}})

  try:
    return (True, native.CallImmutable(frame, code))
  except BaseException as e:  # pylint: disable=broad-except
    return (False, {
        'isError': True,
        'refersTo': 'VARIABLE_VALUE',
        'description': {
            'format': 'Exception occurred: $0',
            'parameters': [e.message]}})


def _GetFrameCodeObjectName(frame):
  """Gets the code object name for the frame.

  Args:
    frame: the frame to get the name from

  Returns:
    The function name if the code is a static function or the class name with
    the method name if it is an member function.
  """
  # This functions under the assumption that member functions will name their
  # first parameter argument 'self' but has some edge-cases.
  if frame.f_code.co_argcount >= 1 and 'self' == frame.f_code.co_varnames[0]:
    return (frame.f_locals['self'].__class__.__name__ +
            '.' + frame.f_code.co_name)
  else:
    return frame.f_code.co_name


def _FormatMessage(template, parameters):
  """Formats the message. Unescapes '$$' with '$'.

  Args:
    template: message template (e.g. 'a = $0, b = $1').
    parameters: substitution parameters for the format.

  Returns:
    Formatted message with parameters embedded in template placeholders.
  """
  def GetParameter(m):
    try:
      return parameters[int(m.group(0)[1:])]
    except IndexError:
      return INVALID_EXPRESSION_INDEX

  parts = template.split('$$')
  return '$'.join(re.sub(r'\$\d+', GetParameter, part) for part in parts)


def _TrimString(s, max_len):
  """Trims the string if it exceeds max_len."""
  if len(s) <= max_len:
    return s
  return s[:max_len+1] + '...'
