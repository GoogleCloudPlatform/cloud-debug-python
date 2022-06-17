"""Set of helper methods for Python debuglet unit and component tests."""

import inspect
import re


def GetModuleInfo(obj):
  """Gets the source file path and breakpoint tags for a module.

  Breakpoint tag is a named label of a source line. The tag is marked
  with "# BPTAG: XXX" comment.

  Args:
    obj: any object inside the queried module.

  Returns:
    (path, tags) tuple where tags is a dictionary mapping tag name to
    line numbers where this tag appears.
  """
  return (inspect.getsourcefile(obj), GetSourceFileTags(obj))


def GetSourceFileTags(source):
  """Gets breakpoint tags for the specified source file.

  Breakpoint tag is a named label of a source line. The tag is marked
  with "# BPTAG: XXX" comment.

  Args:
    source: either path to the .py file to analyze or any code related
        object (e.g. module, function, code object).

  Returns:
    Dictionary mapping tag name to line numbers where this tag appears.
  """
  if isinstance(source, str):
    lines = open(source, 'r').read().splitlines()
    start_line = 1  # line number is 1 based
  else:
    lines, start_line = inspect.getsourcelines(source)
    if not start_line:  # "getsourcelines" returns start_line of 0 for modules.
      start_line = 1

  tags = {}
  regex = re.compile(r'# BPTAG: ([0-9a-zA-Z_]+)\s*$')
  for n, line in enumerate(lines):
    m = regex.search(line)
    if m:
      tag = m.group(1)
      if tag in tags:
        tags[tag].append(n + start_line)
      else:
        tags[tag] = [n + start_line]

  return tags


def ResolveTag(obj, tag):
  """Resolves the breakpoint tag into source file path and a line number.

  Breakpoint tag is a named label of a source line. The tag is marked
  with "# BPTAG: XXX" comment.

  Raises

  Args:
    obj: any object inside the queried module.
    tag: tag name to resolve.

  Raises:
    Exception: if no line in the source file define the specified tag or if
        more than one line define the tag.

  Returns:
    (path, line) tuple, where line is the line number where the tag appears.
  """
  path, tags = GetModuleInfo(obj)
  if tag not in tags:
    raise Exception('tag %s not found' % tag)
  lines = tags[tag]
  if len(lines) != 1:
    raise Exception('tag %s is ambiguous (lines: %s)' % (tag, lines))
  return path, lines[0]


def DateTimeToTimestamp(t):
  """Converts the specified time to Timestamp format.

  Args:
    t: datetime instance

  Returns:
    Time in Timestamp format
  """
  return t.strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'


def DateTimeToTimestampNew(t):
  """Converts the specified time to Timestamp format in seconds granularity.

  Args:
    t: datetime instance

  Returns:
    Time in Timestamp format in seconds granularity
  """
  return t.strftime('%Y-%m-%dT%H:%M:%S') + 'Z'


def PackFrameVariable(breakpoint, name, frame=0, collection='locals'):
  """Finds local variable or argument by name.

  Indirections created through varTableIndex are recursively collapsed. Fails
  the test case if the named variable is not found.

  Args:
    breakpoint: queried breakpoint.
    name: name of the local variable or argument.
    frame: stack frame index to examine.
    collection: 'locals' to get local variable or 'arguments' for an argument.

  Returns:
    Single dictionary of variable data.

  Raises:
    AssertionError: if the named variable not found.
  """
  for variable in breakpoint['stackFrames'][frame][collection]:
    if variable['name'] == name:
      return _Pack(variable, breakpoint)

  raise AssertionError('Variable %s not found in frame %d collection %s' %
                       (name, frame, collection))


def PackWatchedExpression(breakpoint, expression):
  """Finds watched expression by index.

  Indirections created through varTableIndex are recursively collapsed. Fails
  the test case if the named variable is not found.

  Args:
    breakpoint: queried breakpoint.
    expression: index of the watched expression.

  Returns:
    Single dictionary of variable data.
  """
  return _Pack(breakpoint['evaluatedExpressions'][expression], breakpoint)


def _Pack(variable, breakpoint):
  """Recursively collapses indirections created through varTableIndex.

  Circular references by objects are not supported. If variable subtree
  has circular references, this function will hang.

  Variable members are sorted by name. This helps asserting the content of
  variable since Python has no guarantees over the order of keys of a
  dictionary.

  Args:
    variable: variable object to pack. Not modified.
    breakpoint: queried breakpoint.

  Returns:
    A new dictionary with packed variable object.
  """
  packed = dict(variable)

  while 'varTableIndex' in packed:
    ref = breakpoint['variableTable'][packed['varTableIndex']]
    assert 'name' not in ref
    assert 'value' not in packed
    assert 'members' not in packed
    assert 'status' not in ref and 'status' not in packed
    del packed['varTableIndex']
    packed.update(ref)

  if 'members' in packed:
    packed['members'] = sorted(
        [_Pack(m, breakpoint) for m in packed['members']],
        key=lambda m: m.get('name', ''))

  return packed
