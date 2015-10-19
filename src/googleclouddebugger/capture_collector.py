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

import copy
import datetime
import inspect
import os
import sys
import types

import cdbg_native as native

_VECTOR_TYPES = {types.TupleType, types.ListType, types.SliceType, set}

EMPTY_DICTIONARY = 'Empty dictionary'
EMPTY_COLLECTION = 'Empty collection'
OBJECT_HAS_NO_FIELDS = 'Object has no fields'


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
  # that returns None if it doesn't recognize the object or returns iterable
  # enumerating object fields (name-value tuple).
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
            'description': {'format': 'Buffer full'}}}]

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

    # Maximum number of character to allow for a single value. Longer strings
    # are truncated.
    self.max_value_len = 256

    # Maximum number of items in a list to capture.
    self.max_list_items = 25

    # Maximum depth of dictionaries to capture.
    self.max_depth = 5

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
    breakpoint_frames = self.breakpoint['stackFrames']
    while frame and (len(breakpoint_frames) < self.max_frames):
      code = frame.f_code
      if len(breakpoint_frames) < self.max_expand_frames:
        frame_arguments, frame_locals = self.CaptureFrameLocals(frame)
      else:
        frame_arguments = []
        frame_locals = []

      breakpoint_frames.append({
          'function': code.co_name,
          'location': {
              'path': CaptureCollector._NormalizePath(code.co_filename),
              'line': frame.f_lineno},
          'arguments': frame_arguments,
          'locals': frame_locals})
      frame = frame.f_back

    # Evaluate watched expressions.
    if 'expressions' in self.breakpoint:
      self.breakpoint['evaluatedExpressions'] = [
          self._EvaluateExpression(top_frame, expression) for expression
          in self.breakpoint['expressions']]

    # Explore variables table in BFS fashion. The variables table will grow
    # inside CaptureVariable as we encounter new references.
    i = 1
    while (i < len(self._var_table)) and (self._total_size < self.max_size):
      self._var_table[i] = self.CaptureVariable(self._var_table[i], 0, False)
      i += 1

    # Trim variables table and change make all references to variables that
    # didn't make it point to var_index of 0 ("buffer full")
    self.TrimVariableTable(i)

  def CaptureFrameLocals(self, frame):
    """Captures local variables and arguments of the specified frame.

    Args:
      frame: frame to capture locals and arguments.

    Returns:
      (arguments, locals) tuple.
    """
    # Capture all local variables (including method arguments).
    variables = {n: self.CaptureNamedVariable(n, v)
                 for n, v in frame.f_locals.viewitems()}

    # Split between locals and arguments (keeping arguments in the right order).
    nargs = frame.f_code.co_argcount
    if frame.f_code.co_flags & inspect.CO_VARARGS: nargs += 1
    if frame.f_code.co_flags & inspect.CO_VARKEYWORDS: nargs += 1

    frame_arguments = []
    for argname in frame.f_code.co_varnames[:nargs]:
      if argname in variables: frame_arguments.append(variables.pop(argname))

    return (frame_arguments, list(variables.viewvalues()))

  def CaptureNamedVariable(self, name, value, depth=1):
    """Appends name to the product of CaptureVariable.

    Args:
      name: name of the variable.
      value: data to capture
      depth: nested depth of dictionaries and vectors so far.

    Returns:
      Formatted captured data as per Variable proto with name.
    """
    if not hasattr(name, '__dict__'):
      name = str(name)
    else:  # TODO(vlif): call str(name) with immutability verifier here.
      name = str(id(name))
    self._total_size += len(name)

    v = self.CaptureVariable(value, depth)
    v['name'] = name
    return v

  def CaptureVariablesList(self, items, depth, empty_message):
    """Captures list of named items.

    Args:
      items: iterable of (name, value) tuples.
      depth: nested depth of dictionaries and vectors for items.
      empty_message: info status message to set if items is empty.

    Returns:
      List of formatted variable objects.
    """
    v = []
    for name, value in items:
      if (self._total_size >= self.max_size) or (len(v) >= self.max_list_items):
        v.append({
            'status': {
                'refers_to': 'VARIABLE_VALUE',
                'description': {
                    'format': 'Only first $0 items were captured',
                    'parameters': [str(len(v))]}}})
        break
      v.append(self.CaptureNamedVariable(name, value, depth))

    if not v:
      return [{'status': {
          'is_error': False,
          'refers_to': 'VARIABLE_NAME',
          'description': {'format': empty_message}}}]

    return v

  def CaptureVariable(self, value, depth=1, can_enqueue=True):
    """Captures a single nameless object into Variable message.

    TODO(vlif): safely evaluate iterable types.
    TODO(vlif): safely call str(value)

    Args:
      value: data to capture
      depth: nested depth of dictionaries and vectors so far.
      can_enqueue: allows referencing the object in variables table.

    Returns:
      Formatted captured data as per Variable proto.
    """
    if depth == self.max_depth:
      return {'varTableIndex': 0}  # Buffer full.

    if value is None:
      self._total_size += 4
      return {'value': 'None'}

    if isinstance(value, (int, long, float, complex, str, unicode, bool)):
      r = self.TrimString(repr(value))  # Primitive type, always immutable.
      self._total_size += len(r)
      return {'value': r}

    if isinstance(value, (datetime.date, datetime.time, datetime.timedelta)):
      r = str(value)  # Safe to call str().
      self._total_size += len(r)
      return {'value': r}

    if type(value) is dict:
      return {'members': self.CaptureVariablesList(value.iteritems(),
                                                   depth + 1,
                                                   EMPTY_DICTIONARY)}

    if type(value) in _VECTOR_TYPES:
      return {'members': self.CaptureVariablesList(
          (('[%d]' % i, x) for i, x in enumerate(value)),
          depth + 1,
          EMPTY_COLLECTION)}

    if type(value) is types.FunctionType:
      self._total_size += len(value.func_name)
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
      fields = pretty_printer(value)
      if fields:
        return {'members': self.CaptureVariablesList(fields,
                                                     depth + 1,
                                                     OBJECT_HAS_NO_FIELDS)}

    if not hasattr(value, '__dict__'):
      r = str(type(value))
      self._total_size += len(r)
      return {'value': r}

    if not value.__dict__:
      return {'members': [{'status': {
          'is_error': False,
          'refers_to': 'VARIABLE_NAME',
          'description': {'format': OBJECT_HAS_NO_FIELDS}}}]}

    return self.CaptureVariable(value.__dict__, depth + 1)

  def _EvaluateExpression(self, frame, expression):
    """Compiles and evaluates watched expression to a Variable message.

    Args:
      frame: evaluation context.
      expression: watched expression to compile and evaluate.

    Returns:
      Formatted object corresponding to the evaluation result.
    """
    try:
      code = compile(expression, '<watched_expression>', 'eval')
    except TypeError as e:  # condition string contains null bytes.
      return {
          'name': expression,
          'status': {
              'isError': True,
              'refersTo': 'VARIABLE_NAME',
              'description': {
                  'format': 'Invalid expression',
                  'parameters': [str(e)]}}}
    except SyntaxError as e:
      return {
          'name': expression,
          'status': {
              'isError': True,
              'refersTo': 'VARIABLE_NAME',
              'description': {
                  'format': 'Expression could not be compiled: $0',
                  'parameters': [e.msg]}}}

    try:
      value = native.CallImmutable(frame, code)
    except BaseException as e:
      return {
          'name': expression,
          'status': {
              'isError': True,
              'refersTo': 'VARIABLE_VALUE',
              'description': {
                  'format': 'Exception occurred: $0',
                  'parameters': [e.message]}}}
    return self.CaptureNamedVariable(expression, value)

  def TrimString(self, s):
    """Trims the string if it exceeds max_value_len."""
    if len(s) <= self.max_value_len:
      return s
    return s[:self.max_value_len] + '...'

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
    for stack_frame in self.breakpoint['stackFrames']:
      ProcessBufferFull(stack_frame['arguments'])
      ProcessBufferFull(stack_frame['locals'])
    ProcessBufferFull(self._var_table)
    ProcessBufferFull(self.breakpoint['evaluatedExpressions'])

  @staticmethod
  def _NormalizePath(path):
    """Converts an absolute path to a relative one.

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
