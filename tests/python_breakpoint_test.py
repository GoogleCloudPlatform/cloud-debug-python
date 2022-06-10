"""Unit test for python_breakpoint module."""

from datetime import datetime
from datetime import timedelta
import inspect
import os
import sys
import tempfile

from absl.testing import absltest

from googleclouddebugger import cdbg_native as native
from googleclouddebugger import imphook2
from googleclouddebugger import python_breakpoint
import python_test_util


class PythonBreakpointTest(absltest.TestCase):
  """Unit test for python_breakpoint module."""

  def setUp(self):
    self._test_package_dir = tempfile.mkdtemp('', 'package_', absltest.get_default_test_tmpdir())
    sys.path.append(self._test_package_dir)

    path, line = python_test_util.ResolveTag(type(self), 'CODE_LINE')

    self._base_time = datetime(year=2015, month=1, day=1)  # BPTAG: CODE_LINE
    self._template = {
        'id': 'BP_ID',
        'createTime': python_test_util.DateTimeToTimestamp(self._base_time),
        'location': {'path': path, 'line': line}}
    self._completed = set()
    self._update_queue = []

  def tearDown(self):
    sys.path.remove(self._test_package_dir)

  def CompleteBreakpoint(self, breakpoint_id):
    """Mock method of BreakpointsManager."""
    self._completed.add(breakpoint_id)

  def GetCurrentTime(self):
    """Mock method of BreakpointsManager."""
    return self._base_time

  def EnqueueBreakpointUpdate(self, breakpoint):
    """Mock method of HubClient."""
    self._update_queue.append(breakpoint)

  def testClear(self):
    breakpoint = python_breakpoint.PythonBreakpoint(
        self._template, self, self, None)
    breakpoint.Clear()
    self.assertFalse(breakpoint._cookie)

  def testId(self):
    breakpoint = python_breakpoint.PythonBreakpoint(
        self._template, self, self, None)
    breakpoint.Clear()
    self.assertEqual('BP_ID', breakpoint.GetBreakpointId())

  def testNullBytesInCondition(self):
    python_breakpoint.PythonBreakpoint(
        dict(self._template, condition='\0'),
        self,
        self,
        None)
    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertTrue(self._update_queue[0]['status']['isError'])
    self.assertTrue(self._update_queue[0]['isFinalState'])

  # Test only applies to the old module search algorithm. When using new module
  # search algorithm, this test is same as testDeferredBreakpoint.
  def testUnknownModule(self):
    pass

  def testDeferredBreakpoint(self):
    with open(os.path.join(self._test_package_dir, 'defer_print.py'), 'w') as f:
      f.write('def DoPrint():\n')
      f.write('  print("Hello from deferred module")\n')

    python_breakpoint.PythonBreakpoint(
        dict(self._template, location={'path': 'defer_print.py', 'line': 2}),
        self,
        self,
        None)

    self.assertFalse(self._completed)
    self.assertEmpty(self._update_queue)

    import defer_print  # pylint: disable=g-import-not-at-top
    defer_print.DoPrint()

    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertGreater(len(self._update_queue[0]['stackFrames']), 3)
    self.assertEqual(
        'DoPrint',
        self._update_queue[0]['stackFrames'][0]['function'])
    self.assertTrue(self._update_queue[0]['isFinalState'])

    self.assertEmpty(imphook2._import_callbacks)

  # Old module search algorithm rejects multiple matches. This test verifies
  # that the new module search algorithm searches sys.path sequentially, and
  # selects the first match (just like the Python importer).
  def testSearchUsingSysPathOrder(self):
    for i in range(2, 0, -1):
      # Create directories and add them to sys.path.
      test_dir = os.path.join(self._test_package_dir, ('inner2_%s' % i))
      os.mkdir(test_dir)
      sys.path.append(test_dir)
      with open(os.path.join(test_dir, 'mod2.py'), 'w') as f:
        f.write('def DoPrint():\n')
        f.write('  x = %s\n' % i)
        f.write('  return x')

    # Loads inner2_2/mod2.py because it comes first in sys.path.
    import mod2  # pylint: disable=g-import-not-at-top

    # Search will proceed in sys.path order, and the first match in sys.path
    # will uniquely identify the full path of the module as inner2_2/mod2.py.
    python_breakpoint.PythonBreakpoint(
        dict(self._template, location={'path': 'mod2.py', 'line': 3}),
        self,
        self,
        None)

    self.assertEqual(2, mod2.DoPrint())

    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertGreater(len(self._update_queue[0]['stackFrames']), 3)
    self.assertEqual(
        'DoPrint',
        self._update_queue[0]['stackFrames'][0]['function'])
    self.assertTrue(self._update_queue[0]['isFinalState'])
    self.assertEqual(
        'x',
        self._update_queue[0]['stackFrames'][0]['locals'][0]['name'])
    self.assertEqual(
        '2',
        self._update_queue[0]['stackFrames'][0]['locals'][0]['value'])

    self.assertEmpty(imphook2._import_callbacks)

  # Old module search algorithm rejects multiple matches. This test verifies
  # that when the new module search cannot find any match in sys.path, it
  # defers the breakpoint, and then selects the first dynamically-loaded
  # module that matches the given path.
  def testMultipleDeferredMatches(self):
    for i in range(2, 0, -1):
      # Create packages, but do not add them to sys.path.
      test_dir = os.path.join(self._test_package_dir, ('inner3_%s' % i))
      os.mkdir(test_dir)
      with open(os.path.join(test_dir, '__init__.py'), 'w') as f:
        pass
      with open(os.path.join(test_dir, 'defer_print3.py'), 'w') as f:
        f.write('def DoPrint():\n')
        f.write('  x = %s\n' % i)
        f.write('  return x')

    # This breakpoint will be deferred. It can match any one of the modules
    # created above.
    python_breakpoint.PythonBreakpoint(
        dict(self._template, location={'path': 'defer_print3.py', 'line': 3}),
        self,
        self,
        None)

    # Lazy import module. Activates breakpoint on the loaded module.
    import inner3_1.defer_print3  # pylint: disable=g-import-not-at-top
    self.assertEqual(1, inner3_1.defer_print3.DoPrint())

    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertGreater(len(self._update_queue[0]['stackFrames']), 3)
    self.assertEqual(
        'DoPrint',
        self._update_queue[0]['stackFrames'][0]['function'])
    self.assertTrue(self._update_queue[0]['isFinalState'])
    self.assertEqual(
        'x',
        self._update_queue[0]['stackFrames'][0]['locals'][0]['name'])
    self.assertEqual(
        '1',
        self._update_queue[0]['stackFrames'][0]['locals'][0]['value'])

    self.assertEmpty(imphook2._import_callbacks)

  def testNeverLoadedBreakpoint(self):
    open(os.path.join(self._test_package_dir, 'never_print.py'), 'w').close()

    breakpoint = python_breakpoint.PythonBreakpoint(
        dict(self._template, location={'path': 'never_print.py', 'line': 99}),
        self,
        self,
        None)
    breakpoint.Clear()

    self.assertFalse(self._completed)
    self.assertEmpty(self._update_queue)

  def testDeferredNoCodeAtLine(self):
    open(os.path.join(self._test_package_dir, 'defer_empty.py'), 'w').close()

    python_breakpoint.PythonBreakpoint(
        dict(self._template, location={'path': 'defer_empty.py', 'line': 10}),
        self,
        self,
        None)

    self.assertFalse(self._completed)
    self.assertEmpty(self._update_queue)

    import defer_empty  # pylint: disable=g-import-not-at-top,unused-variable

    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertTrue(self._update_queue[0]['isFinalState'])
    status = self._update_queue[0]['status']
    self.assertEqual(status['isError'], True)
    self.assertEqual(status['refersTo'], 'BREAKPOINT_SOURCE_LOCATION')
    desc = status['description']
    self.assertEqual(desc['format'], 'No code found at line $0 in $1')
    params = desc['parameters']
    self.assertIn('defer_empty.py', params[1])
    self.assertEqual(params[0], '10')
    self.assertEmpty(imphook2._import_callbacks)

  def testDeferredBreakpointCancelled(self):
    open(os.path.join(self._test_package_dir, 'defer_cancel.py'), 'w').close()

    breakpoint = python_breakpoint.PythonBreakpoint(
        dict(self._template, location={'path': 'defer_cancel.py', 'line': 11}),
        self,
        self,
        None)
    breakpoint.Clear()

    self.assertFalse(self._completed)
    self.assertEmpty(imphook2._import_callbacks)
    unused_no_code_line_above = 0  # BPTAG: NO_CODE_LINE_ABOVE

  # BPTAG: NO_CODE_LINE
  def testNoCodeAtLine(self):
    unused_no_code_line_below = 0  # BPTAG: NO_CODE_LINE_BELOW
    path, line = python_test_util.ResolveTag(sys.modules[__name__],
                                             'NO_CODE_LINE')
    path, line_above = python_test_util.ResolveTag(sys.modules[__name__],
                                                   'NO_CODE_LINE_ABOVE')
    path, line_below = python_test_util.ResolveTag(sys.modules[__name__],
                                                   'NO_CODE_LINE_BELOW')

    python_breakpoint.PythonBreakpoint(
        dict(self._template, location={'path': path, 'line': line}),
        self,
        self,
        None)
    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertTrue(self._update_queue[0]['isFinalState'])
    status = self._update_queue[0]['status']
    self.assertEqual(status['isError'], True)
    self.assertEqual(status['refersTo'], 'BREAKPOINT_SOURCE_LOCATION')
    desc = status['description']
    self.assertEqual(desc['format'],
                     'No code found at line $0 in $1. Try lines $2 or $3.')
    params = desc['parameters']
    self.assertEqual(params[0], str(line))
    self.assertIn(path, params[1])
    self.assertEqual(params[2], str(line_above))
    self.assertEqual(params[3], str(line_below))

  def testBadExtension(self):
    for path in ['unknown.so', 'unknown', 'unknown.java', 'unknown.pyc']:
      python_breakpoint.PythonBreakpoint(
          dict(self._template, location={'path': path, 'line': 83}),
          self,
          self,
          None)
      self.assertEqual(set(['BP_ID']), self._completed)
      self.assertLen(self._update_queue, 1)
      self.assertTrue(self._update_queue[0]['isFinalState'])
      self.assertEqual(
          {'isError': True,
           'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
           'description': {
               'format': ('Only files with .py extension are supported')}},
          self._update_queue[0]['status'])
      self._update_queue = []

  def testRootInitFile(self):
    for path in ['__init__.py', '/__init__.py', '////__init__.py',
                 ' __init__.py ', ' //__init__.py']:
      python_breakpoint.PythonBreakpoint(
          dict(self._template, location={'path': path, 'line': 83}),
          self,
          self,
          None)
      self.assertEqual(set(['BP_ID']), self._completed)
      self.assertLen(self._update_queue, 1)
      self.assertTrue(self._update_queue[0]['isFinalState'])
      self.assertEqual(
          {'isError': True,
           'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
           'description': {
               'format':
                   'Multiple modules matching $0. '
                   'Please specify the module path.',
               'parameters': ['__init__.py']
           }},
          self._update_queue[0]['status'])
      self._update_queue = []

  # Old module search algorithm rejects because there are too many matches.
  # The new algorithm selects the very first match in sys.path.
  def testNonRootInitFile(self):
    # Neither 'a' nor 'a/b' are real packages accessible via sys.path.
    # Therefore, module search falls back to search '__init__.py', which matches
    # the first entry in sys.path, which we artifically inject below.
    test_dir = os.path.join(self._test_package_dir, 'inner4')
    os.mkdir(test_dir)
    with open(os.path.join(test_dir, '__init__.py'), 'w') as f:
      f.write('def DoPrint():\n')
      f.write('  print("Hello")')
    sys.path.insert(0, test_dir)

    import inner4  # pylint: disable=g-import-not-at-top,unused-variable

    for path in ['/a/__init__.py', 'a/__init__.py', 'a/b/__init__.py']:
      python_breakpoint.PythonBreakpoint(
          dict(self._template, location={'path': path, 'line': 2}),
          self,
          self,
          None)

      inner4.DoPrint()

      self.assertEqual(set(['BP_ID']), self._completed)
      self.assertLen(self._update_queue, 1)
      self.assertTrue(self._update_queue[0]['isFinalState'])
      self.assertGreater(len(self._update_queue[0]['stackFrames']), 3)
      self.assertEqual(
          'DoPrint',
          self._update_queue[0]['stackFrames'][0]['function'])

      self.assertEmpty(imphook2._import_callbacks)
      self._update_queue = []

  def testBreakpointInLoadedPackageFile(self):
    """Test breakpoint in a loaded package."""
    for name in ['pkg', 'pkg/pkg']:
      test_dir = os.path.join(self._test_package_dir, name)
      os.mkdir(test_dir)
      with open(os.path.join(test_dir, '__init__.py'), 'w') as f:
        f.write('def DoPrint():\n')
        f.write('  print("Hello from %s")\n' % name)

    import pkg  # pylint: disable=g-import-not-at-top,unused-variable
    import pkg.pkg  # pylint: disable=g-import-not-at-top,unused-variable

    python_breakpoint.PythonBreakpoint(
        dict(self._template,
             location={'path': 'pkg/pkg/__init__.py', 'line': 2}),
        self,
        self,
        None)

    pkg.pkg.DoPrint()

    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertTrue(self._update_queue[0]['isFinalState'])
    self.assertEqual(None, self._update_queue[0].get('status'))
    self._update_queue = []

  def testInternalError(self):
    """Simulate internal error when setting a new breakpoint.

    Bytecode rewriting breakpoints are not supported for methods with more
    than 65K constants. We generate such a method and try to set breakpoint in
    it.
    """

    with open(os.path.join(self._test_package_dir, 'intern_err.py'), 'w') as f:
      f.write('def DoSums():\n')
      f.write('  x = 0\n')
      for i in range(70000):
        f.write('  x = x + %d\n' % i)
      f.write('  print(x)\n')

    import intern_err  # pylint: disable=g-import-not-at-top,unused-variable

    python_breakpoint.PythonBreakpoint(
        dict(self._template, location={'path': 'intern_err.py', 'line': 100}),
        self,
        self,
        None)

    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertEqual(
        {'isError': True,
         'description': {'format': 'Internal error occurred'}},
        self._update_queue[0]['status'])

  def testInvalidCondition(self):
    python_breakpoint.PythonBreakpoint(
        dict(self._template, condition='2+'),
        self,
        self,
        None)
    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertTrue(self._update_queue[0]['isFinalState'])
    self.assertEqual(
        {'isError': True,
         'refersTo': 'BREAKPOINT_CONDITION',
         'description': {
             'format': 'Expression could not be compiled: $0',
             'parameters': ['unexpected EOF while parsing']}},
        self._update_queue[0]['status'])

  def testHit(self):
    breakpoint = python_breakpoint.PythonBreakpoint(
        self._template, self, self, None)
    breakpoint._BreakpointEvent(
        native.BREAKPOINT_EVENT_HIT,
        inspect.currentframe())
    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertGreater(len(self._update_queue[0]['stackFrames']), 3)
    self.assertTrue(self._update_queue[0]['isFinalState'])

  def testHitNewTimestamp(self):
    # Override to use the new format (i.e., without the '.%f' sub-second part)
    self._template['createTime'] = python_test_util.DateTimeToTimestampNew(
        self._base_time)

    breakpoint = python_breakpoint.PythonBreakpoint(
        self._template, self, self, None)
    breakpoint._BreakpointEvent(
        native.BREAKPOINT_EVENT_HIT,
        inspect.currentframe())
    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertGreater(len(self._update_queue[0]['stackFrames']), 3)
    self.assertTrue(self._update_queue[0]['isFinalState'])

  def testDoubleHit(self):
    breakpoint = python_breakpoint.PythonBreakpoint(
        self._template, self, self, None)
    breakpoint._BreakpointEvent(
        native.BREAKPOINT_EVENT_HIT,
        inspect.currentframe())
    breakpoint._BreakpointEvent(
        native.BREAKPOINT_EVENT_HIT,
        inspect.currentframe())
    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)

  def testEndToEndUnconditional(self):
    def Trigger():
      pass  # BPTAG: E2E_UNCONDITIONAL

    path, line = python_test_util.ResolveTag(type(self), 'E2E_UNCONDITIONAL')
    breakpoint = python_breakpoint.PythonBreakpoint(
        {'id': 'BP_ID',
         'location': {'path': path, 'line': line}},
        self,
        self,
        None)
    self.assertEmpty(self._update_queue)
    Trigger()
    self.assertLen(self._update_queue, 1)
    breakpoint.Clear()

  def testEndToEndConditional(self):
    def Trigger():
      for i in range(2):
        self.assertLen(self._update_queue, i)  # BPTAG: E2E_CONDITIONAL

    path, line = python_test_util.ResolveTag(type(self), 'E2E_CONDITIONAL')
    breakpoint = python_breakpoint.PythonBreakpoint(
        {'id': 'BP_ID',
         'location': {'path': path, 'line': line},
         'condition': 'i == 1'},
        self,
        self,
        None)
    Trigger()
    breakpoint.Clear()

  def testEndToEndCleared(self):
    path, line = python_test_util.ResolveTag(type(self), 'E2E_CLEARED')
    breakpoint = python_breakpoint.PythonBreakpoint(
        {'id': 'BP_ID',
         'location': {'path': path, 'line': line}},
        self,
        self,
        None)
    breakpoint.Clear()
    self.assertEmpty(self._update_queue)  # BPTAG: E2E_CLEARED

  def testBreakpointCancellationEvent(self):
    events = [
        native.BREAKPOINT_EVENT_GLOBAL_CONDITION_QUOTA_EXCEEDED,
        native.BREAKPOINT_EVENT_BREAKPOINT_CONDITION_QUOTA_EXCEEDED,
        native.BREAKPOINT_EVENT_CONDITION_EXPRESSION_MUTABLE]
    for event in events:
      breakpoint = python_breakpoint.PythonBreakpoint(
          self._template,
          self,
          self,
          None)
      breakpoint._BreakpointEvent(event, None)
      self.assertLen(self._update_queue, 1)
      self.assertEqual(set(['BP_ID']), self._completed)

      self._update_queue = []
      self._completed = set()

  def testExpirationTime(self):
    breakpoint = python_breakpoint.PythonBreakpoint(
        self._template, self, self, None)
    breakpoint.Clear()
    self.assertEqual(
        datetime(year=2015, month=1, day=2),
        breakpoint.GetExpirationTime())

  def testExpirationTimeWithExpiresIn(self):
    definition = self._template.copy()
    definition['expires_in'] = {
        'seconds': 300  # 5 minutes
    }

    breakpoint = python_breakpoint.PythonBreakpoint(
        definition, self, self, None)
    breakpoint.Clear()
    self.assertEqual(
        datetime(year=2015, month=1, day=2),
        breakpoint.GetExpirationTime())

  def testExpiration(self):
    breakpoint = python_breakpoint.PythonBreakpoint(
        self._template, self, self, None)
    breakpoint.ExpireBreakpoint()
    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertTrue(self._update_queue[0]['isFinalState'])
    self.assertEqual(
        {'isError': True,
         'refersTo': 'BREAKPOINT_AGE',
         'description': {'format': 'The snapshot has expired'}},
        self._update_queue[0]['status'])

  def testLogpointExpiration(self):
    definition = self._template.copy()
    definition['action'] = 'LOG'
    breakpoint = python_breakpoint.PythonBreakpoint(
        definition, self, self, None)
    breakpoint.ExpireBreakpoint()
    self.assertEqual(set(['BP_ID']), self._completed)
    self.assertLen(self._update_queue, 1)
    self.assertTrue(self._update_queue[0]['isFinalState'])
    self.assertEqual(
        {'isError': True,
         'refersTo': 'BREAKPOINT_AGE',
         'description': {'format': 'The logpoint has expired'}},
        self._update_queue[0]['status'])

  def testNormalizePath(self):
    # Removes leading '/' character.
    for path in ['/__init__.py', '//__init__.py', '////__init__.py']:
      self.assertEqual('__init__.py', python_breakpoint._NormalizePath(path))

    # Removes leading and trailing whitespace.
    for path in [' __init__.py', '__init__.py ', '  __init__.py  ']:
      self.assertEqual('__init__.py', python_breakpoint._NormalizePath(path))

    # Removes combination of leading/trailing whitespace and '/' character.
    for path in ['  /__init__.py', '  ///__init__.py', '////__init__.py']:
      self.assertEqual('__init__.py', python_breakpoint._NormalizePath(path))

    # Normalizes the relative path.
    for path in ['  ./__init__.py', '././__init__.py', ' .//abc/../__init__.py',
                 '  ///abc///..///def/..////__init__.py']:
      self.assertEqual('__init__.py', python_breakpoint._NormalizePath(path))

    # Does not remove non-leading, non-trailing space, or non-leading '/'
    # characters.
    self.assertEqual(
        'foo bar/baz/__init__.py',
        python_breakpoint._NormalizePath('/foo bar/baz/__init__.py'))
    self.assertEqual(
        'foo/bar baz/__init__.py',
        python_breakpoint._NormalizePath('/foo/bar baz/__init__.py'))
    self.assertEqual(
        'foo/bar/baz/__in it__.py',
        python_breakpoint._NormalizePath('/foo/bar/baz/__in it__.py'))

if __name__ == '__main__':
  absltest.main()
