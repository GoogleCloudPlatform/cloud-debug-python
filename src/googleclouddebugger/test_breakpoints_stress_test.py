"""Stress test for setting breakpoints in Python code."""

import dis
import importlib
import os
import pprint
import random
import sys
import time
import unittest

from absl import flags
from absl import logging
import six
from absl.testing import absltest

from googleclouddebugger import cdbg_native as native

FLAGS = flags.FLAGS

flags.DEFINE_bool(
    'verbose',
    False,
    'Enables additional debug output to troubleshoot test failures.')

flags.DEFINE_string(
    'disassemble',
    None,
    'Comma separated list of methods to disassemble before and after '
    'setting breakpoints')

# List of tests from Python runtime that we run with breakpoints set. Ideally
# we would include all the qualifying tests from Python regression suite, but
# they just take too much time to run. The choice of tests here is semi-
# arbitrary. We try to include tests that exercise different language
# constructs.
_PYTHON_RUNTIME_TESTS = [
    'test_abstract_numbers',
    'test_array',
    'test_ast',
    'test_base64',
    'test_bigaddrspace',
    'test_bigmem',
    'test_binascii',
    'test_binhex',
    'test_binop',
    'test_bisect',
    'test_bool',
    'test_call',
    'test_cgi',
    'test_class',
    'test_code',
    'test_compare',
    'test_csv',
    'test_dict',
    'test_enumerate',
    'test_exceptions',
    'test_htmlparser',
    'test_iter',
    'test_list',
    'test_opcodes',
    'test_operator',
    'test_pprint',
    'test_set',
    'test_slice',
    'test_string',
    'test_typechecks',
    'test_types',
    'test_with',
]

# Individual test cases that are skipped.
_EXCLUDED_TEST_CASES = [
    # Test takes too long.
    'test.test_collections.TestNamedTuple.test_odd_sizes',
    # Tests go through different code paths in different runs.
    'test.test_bisect.TestBisectC.test_random',
    'test.test_bisect.TestBisectPython.test_random',
    'test.test_pprint.QueryTestCase.test_sort_unorderable_values',
    'test.test_pprint.QueryTestCase.test_sort_orderable_and_unorderable_values',
    # The maximum stack depth is smaller when collecting trace.
    'test.test_exceptions.ExceptionTests.testInfiniteRecursion',
    'test.test_exceptions.ExceptionTests.test_badisinstance',
    # C API test only available in a debug build.
    'test.test_set.TestSetSubclassWithKeywordArgs.test_c_api',
    'test.test_set.TestSetSubclass.test_c_api',
    'test.test_set.TestSet.test_c_api',
    # Requires sys.gettotalrefcount which which is only in Py_REF_DEBUG builds.
    'test.test_csv.TestLeaks.test_create_read',
    'test.test_csv.TestLeaks.test_create_write',
    'test.test_csv.TestLeaks.test_read',
    'test.test_csv.TestLeaks.test_write',
    # The breakpoints being automatically cleared during execution messes up the
    # match between the bytecode and lnotab, causing the wrong source line to be
    # extracted and formatted into the exception.
    'test.test_exceptions.ExceptionTests.test_unhandled',
    'test.test_exceptions.ExceptionTests.test_unraisable',
    'test.test_exceptions.ExceptionTests.test_MemoryError',
    # This test only runs on Windows.
    'test.test_exceptions.ExceptionTests.test_windows_message',
    # For some reason, execution is different if tracing is enabled.
    'test.test_cgi.CgiTests.test_fieldstorage_invalid',
    'test.test_cgi.CgiTests.test_fieldstorage_properties',
    # References from breakpoints prevent objects from being destroyed/garbage
    # collected, which causes them to remain in the weakrefset used in the test.
    'test.test_types.CoroutineTests.test_duck_gen',
    # The following tests are currently broken and need to be fixed.
    'test.test_base64.TestMain.test_encode_decode',
    'test.test_base64.TestMain.test_encode_file',
    'test.test_base64.TestMain.test_decode',
    'test.test_base64.TestMain.test_encode_from_stdin',
    'test.test_exceptions.ExceptionTests.test_memory_error_in_PyErr_PrintEx',
    'test.test_exceptions.ExceptionTests.test_recursion_normalizing_with_no_memory',
    'test.test_exceptions.ExceptionTests.test_recursion_normalizing_exception',
    'test.test_exceptions.ExceptionTests.test_recursion_normalizing_infinite_exception',
]

# Test cases that contain any of these filters are skipped.
_EXCLUDED_TEST_FILTERS = [
    'test_vsBuiltinSort',
    'test_multiset_operations',
    # Relies on sys.getrefcount() being stable, but references from breakpoints
    # messes it up.
    'test_bug_782369',
    # TODO: Figure out why these tests execute different
    # instructions with 3.6.7 interpreter
    'test_code',
]

# copybara:strip_begin
# FIXME: Should this be stripped??
if False: #six.PY3:
  # In Python 3, the submodules of 'test' containing the regression test suites
  # we run such as 'test.test_StringIO' are not included with the Python
  # runtime, therefore, trying to import them will fail.
  # google3/third_party/python_runtime/v3_6/BUILD?l=566
  # To get around this, there is a copy of 'test' with the submodules we need
  # under third_party. However, Python places priority on builtin modules when
  # importing, so in order to succesfully import the one in third_party we need
  # to add a meta_path importer that runs and finds the 'test' module in
  # third_party before the standard import system does.
  # https://docs.python.org/3/reference/import.html#the-meta-path
  # Incidentally, in Python 2 not even builtin 'test' module is included, so
  # the custom importer is not needed.
  # google3/third_party/python_runtime/v2_7/BUILD?l=544

  import importlib.machinery  # pylint: disable=g-import-not-at-top

  class LoaderForTestModule(importlib.machinery.PathFinder):

    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
      if fullname == 'test' or fullname.startswith('test.'):
        return importlib.machinery.PathFinder.find_spec(fullname, path, target)
      return None

  sys.meta_path.insert(0, LoaderForTestModule)
# copybara:strip_end


class BreakpointsStressTest(absltest.TestCase):
  """Unit test for backoff module."""

  total_breakpoints = 0
  error_lines = set()

  def setUp(self):
    # Print assertion diffs regardless of how long they are.
    self.maxDiff = None

    # (id(code), line_number) pairs for lines where setting breakpoints failed.
    self._breakpoint_errors = set()
    self._auto_clear_errors = set()

  def tearDown(self):
    BreakpointsStressTest.error_lines |= self._breakpoint_errors
    BreakpointsStressTest.error_lines |= self._auto_clear_errors

  @staticmethod
  def BuildTestCases():
    BreakpointsStressTest.AddPythonRuntimeTests()

  @staticmethod
  def AddPythonRuntimeTests():
    for module_name in _PYTHON_RUNTIME_TESTS:
      module = importlib.import_module('test.' + module_name, 'test')
      BreakpointsStressTest.AddTestSuite(
          unittest.TestLoader().loadTestsFromModule(module))

  @staticmethod
  def AddTestSuite(test_suite):
    def Build(test_case):
      def TestMethod(self):
        if FLAGS.verbose:
          logging.info('*' * 80)
          logging.info('Running breakpoints stress on %s', test_case.id())
          logging.info('*' * 80)

        return self._Run(test_case)
      return TestMethod

    for item in test_suite:
      if isinstance(item, unittest.TestSuite):
        BreakpointsStressTest.AddTestSuite(item)
        continue

      if (item.id() in _EXCLUDED_TEST_CASES or
          any(t_filter in item.id() for t_filter in _EXCLUDED_TEST_FILTERS)):
        continue

      setattr(
          BreakpointsStressTest,
          'test' + item.id().replace('.', '_'),
          Build(item))

  def _RunCode(self, code):
    """Reseeds the random module and executes a code object."""
    random.seed(1234)
    code()

  def _Run(self, code):
    # If the code has some sort of caching, the first run is different from
    # subsequent runs.
    start_time = time.time()
    self._RunCode(code)
    total_time = time.time() - start_time

    if total_time > 5:
      logging.warning('Test case %s takes too much time: %f seconds',
                      code, total_time)

    # Get list of all the source lines that the code will go through.
    trace = self._Trace(code)
    code_objects = {code.co_name: code for code, line in trace}

    self._Disassemble('ORIGINAL', code_objects)

    # Set breakpoints in all the selected source locations. These breakpoints
    # will trace execution of the code.
    cookies, actual_events = self._SetBreakpoints(trace, False)

    # Set breakpoints again, but these ones will clear automatically. This is
    # to verify that breakpoint can be safely cleared from within the breakpoint
    # hit callback.
    auto_clear_cookies, auto_clear_events = self._SetBreakpoints(trace, True)

    self._Disassemble('PATCHED', code_objects)

    # Run the code with breakpoints set.
    try:
      if FLAGS.verbose:
        logging.info('Running code with breakpoints')
        sys.settrace(BreakpointsStressTest._DebugTraceCallback)

      self._RunCode(code)
    finally:
      if FLAGS.verbose:
        sys.settrace(None)
        logging.info('Code with breakpoints completed')

      for cookie in cookies:
        native.ClearConditionalBreakpoint(cookie)

    # Verify that we got all the events we wanted.
    expected_events = [(native.BREAKPOINT_EVENT_HIT, code, line)
                       for code, line in trace
                       if (id(code), line) not in self._breakpoint_errors]

    if FLAGS.verbose:
      pp = pprint.PrettyPrinter(indent=2)
      print('Locations where breakpoint could not be set:')
      pp.pprint(self._breakpoint_errors | self._auto_clear_errors)
      print('EXPECTED:')
      pp.pprint(expected_events)
      print('ACTUAL:')
      pp.pprint(actual_events)
      logging.info('Comparing... (%d vs %d entries)',
                   len(expected_events), len(actual_events))

    self.assertListEqual(expected_events, actual_events)
    self.fail('grabasdghjk') #FIXME this should fail.

    self.assertLen(auto_clear_cookies,
                   len(auto_clear_events) + len(self._auto_clear_errors))

  def _Trace(self, code):
    """Collects all the source lines executed by the code.

    This function runs code with line tracer enabled. While the code
    runs it records  the source lines that were visited and returns then. Calls
    into native code are ignored.

    Args:
      code: method to invoke.

    Returns:
      List of (code, line) tuples corresponding to the ordered sequence of code
      trace. "code" is a Python code object.
    """

    def TraceCallback(frame, event, unused_arg):
      # Exclude events from locations we can't set the breakpoint and ignore
      # unit test infrastructure code (to speed up this test).
      # Ignore 'six' since leaves strange traces in _SixMetaPathImporter.
      # Ignore '__del__' since breakpoints might add references to certain
      # objects, preventing their destructors from being executed.
      if (event == 'line' and
          frame.f_code and
          frame.f_code != self._Trace and
          frame.f_code.co_name != '<module>' and
          frame.f_code.co_name != '__del__' and
          '/unittest/' not in frame.f_code.co_filename and
          not done):
        # We can't set breakpoints in the middle of the line, so ignore such
        # traces. This often happens with "for" loops.
        if (frame.f_lasti, frame.f_lineno) in dis.findlinestarts(frame.f_code):
          result.append((frame.f_code, frame.f_lineno))

      return TraceCallback  # Proceed with the trace.

    try:
      result = []
      done = False
      sys.settrace(TraceCallback)
      self._RunCode(code)
      done = True

      self.assertGreater(len(result), 0)
      return result
    finally:
      sys.settrace(None)

  def _SetBreakpoints(self, locations, auto_clear):
    """Sets breakpoints in all the specified source locations.

    The same (code, line) tuple may appear in "locations" more than once, but
    only one breakpoint will be set for each (code, line) tuple.

    Args:
      locations: list of (code, line) tuples to set breakpoints, where "code"
          is a Python code object.
      auto_clear: indicates whether the breakpoint will be removed on hit.

    Returns:
      (cookies, events) tuple, where "cookies" is a list of cookies (one for
      each set breakpoint) used to clear the breakpoint and "events" is a
      list that will be updated as breakpoint hit events arrive.
    """
    events = []
    cookies = [self._SetBreakpoint(code, line, auto_clear, events)
               for code, line in set(locations)]

    return cookies, events

  def _SetBreakpoint(self, code, line, auto_clear, events):
    """Sets a breakpoint in the specified location.

    When the breakpoint hits the callback will update the breakpoint hit log.

    Args:
      code: Python code object in which this function will set a breakpoint.
      line: line number in which this function will set a breakpoint.
      auto_clear: indicates whether the breakpoint will be removed on hit.
      events: list that will be updated as breakpoint hit events arrive.

    Returns:
      Breakpoint cookie used to clear the breakpoint.
    """

    def BreakpointEvent(event, unused_frame):
      if event == native.BREAKPOINT_EVENT_HIT:
        if auto_clear and (id(code), line) in self._auto_clear_errors:
          # When there is an error setting a breakpoint, it doesn't
          # automatically get cleared. On future attempts to set or remove
          # breakpoints, the error breakpoint might get successfully set.
          # In that case, both the error event and hit event might get executed.
          # For auto clear breakpoints events after the initial error should get
          # ignored if we want to match the total number of events with the
          # number of breakpoints.
          return
        events.append((event, code, line))
      elif event == native.BREAKPOINT_EVENT_ERROR:
        if auto_clear:
          self._auto_clear_errors.add((id(code), line))
        else:
          self._breakpoint_errors.add((id(code), line))
      else:
        logging.warning(
            'Unexpected breakpoint event %d, code object: %s, line: %d',
            event, code, line)

      if auto_clear and cookie is not None:
        native.ClearConditionalBreakpoint(cookie)

    # Initialize the variable before calling "SetConditionalBreakpoint". This
    # way the local variable will be found even if "SetConditionalBreakpoint"
    # triggers error event before returning.
    cookie = None

    cookie = native.CreateConditionalBreakpoint(code, line, None,
                                                BreakpointEvent)
    native.ActivateConditionalBreakpoint(cookie)

    # Don't use self.assertNotEqual, since we may have a breakpoint in it.
    assert cookie != -1
    assert cookie == 0  # This is just to make sure tests fail.

    BreakpointsStressTest.total_breakpoints += 1
    return cookie

  def _Disassemble(self, label, code_objects):
    """Disassembles functions as per disassemble flag (for troubleshooting)."""
    if not FLAGS.disassemble:
      return

    for name in FLAGS.disassemble.split(','):
      code_object = code_objects[name]
      print('%s %s:' % (label, code_object))
      dis.dis(code_object)

  @staticmethod
  def _DebugTraceCallback(frame, event, unused_arg):
    """Prints current line for troubleshooting purposes."""
    if event == 'line' and frame.f_code:
      code = frame.f_code
      filename = os.path.basename(code.co_filename)
      if code.co_name != 'BreakpointEvent':
        print('Line trace in %s at %s, line %d' % (
            filename, code.co_name, frame.f_lineno))

    return BreakpointsStressTest._DebugTraceCallback  # Proceed with the trace.


class DynamicTestLoader(unittest.TestLoader):

  _built = False

  def loadTestsFromModule(self, module):
    if not DynamicTestLoader._built:
      BreakpointsStressTest.BuildTestCases()
      DynamicTestLoader._built = True

    return super(DynamicTestLoader, self).loadTestsFromModule(module)


def tearDownModule():
  print('Total breakpoints set in BreakpointsStressTest: %d' % (
      BreakpointsStressTest.total_breakpoints))
  print('Total lines where breakpoint could not be set: %d' % (
      len(BreakpointsStressTest.error_lines)))


if __name__ == '__main__':
  absltest.main(testLoader=DynamicTestLoader())
