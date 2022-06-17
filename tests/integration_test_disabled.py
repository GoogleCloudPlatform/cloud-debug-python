"""Complete tests of the debugger mocking the backend."""

# TODO: Get this test to work well all supported versions of python.

from datetime import datetime
from datetime import timedelta
import functools
import inspect
import itertools
import os
import sys
import time
from unittest import mock

from googleapiclient import discovery
import googleclouddebugger as cdbg

from six.moves import queue

import google.auth
from absl.testing import absltest

from googleclouddebugger import capture_collector
from googleclouddebugger import labels
import python_test_util

_TEST_DEBUGGEE_ID = 'gcp:integration-test-debuggee-id'
_TEST_AGENT_ID = 'agent-id-123-abc'
_TEST_PROJECT_ID = 'test-project-id'
_TEST_PROJECT_NUMBER = '123456789'

# Time to sleep before returning the result of an API call.
# Without a delay, the agent will continuously call ListActiveBreakpoints,
# and the mock object will use a lot of memory to record all the calls.
_REQUEST_DELAY_SECS = 0.01


class IntegrationTest(absltest.TestCase):
  """Complete tests of the debugger mocking the backend.

  These tests employ all the components of the debugger. The actual
  communication channel with the backend is mocked. This allows the test
  quickly inject breakpoints and read results. It also makes the test
  standalone and independent of the actual backend.

  Uses the new module search algorithm (b/70226488).
  """

  class FakeHub(object):
    """Starts the debugger with a mocked communication channel."""

    def __init__(self):
      # Breakpoint updates posted by the debugger that haven't been processed
      # by the test case code.
      self._incoming_breakpoint_updates = queue.Queue()

      # Running counter used to generate unique breakpoint IDs.
      self._id_counter = itertools.count()

      self._service = mock.Mock()

      patcher = mock.patch.object(discovery, 'build')
      self._mock_build = patcher.start()
      self._mock_build.return_value = self._service

      patcher = mock.patch.object(google.auth, 'default')
      self._default_auth_mock = patcher.start()
      self._default_auth_mock.return_value = None, _TEST_PROJECT_ID

      controller = self._service.controller.return_value
      debuggees = controller.debuggees.return_value
      breakpoints = debuggees.breakpoints.return_value

      # Simulate a time delay for calls to the mock API.
      def ReturnWithDelay(val):

        def GetVal():
          time.sleep(_REQUEST_DELAY_SECS)
          return val

        return GetVal

      self._register_execute = debuggees.register.return_value.execute
      self._register_execute.side_effect = ReturnWithDelay({
          'debuggee': {
              'id': _TEST_DEBUGGEE_ID
          },
          'agentId': _TEST_AGENT_ID
      })

      self._active_breakpoints = {'breakpoints': []}
      self._list_execute = breakpoints.list.return_value.execute
      self._list_execute.side_effect = ReturnWithDelay(self._active_breakpoints)

      breakpoints.update = self._UpdateBreakpoint

      # Start the debugger.
      cdbg.enable()

      # Increase the polling rate to speed up the test.
      cdbg._hub_client.min_interval_sec = 0.001  # Poll every 1 ms

    def SetBreakpoint(self, tag, template=None):
      """Sets a new breakpoint in this source file.

      The line number is identified by tag. The optional template may specify
      other breakpoint parameters such as condition and watched expressions.

      Args:
        tag: label for a source line.
        template: optional breakpoint parameters.
      """
      path, line = python_test_util.ResolveTag(sys.modules[__name__], tag)
      self.SetBreakpointAtPathLine(path, line, template)

    def SetBreakpointAtFile(self, filename, tag, template=None):
      """Sets a breakpoint in a file with the given filename.

      The line number is identified by tag. The optional template may specify
      other breakpoint parameters such as condition and watched expressions.

      Args:
        filename: the name of the file inside which the tag will be searched.
                  Must be in the same directory as the current file.
        tag: label for a source line.
        template: optional breakpoint parameters.

      Raises:
        Exception: when the given tag does not uniquely identify a line.
      """
      # TODO: Move part of this to python_test_utils.py file.
      # Find the full path of filename, using the directory of the current file.
      module_path = inspect.getsourcefile(sys.modules[__name__])
      directory, unused_name = os.path.split(module_path)
      path = os.path.join(directory, filename)

      # Similar to ResolveTag(), but for a module that's not loaded yet.
      tags = python_test_util.GetSourceFileTags(path)
      if tag not in tags:
        raise Exception('tag %s not found' % tag)
      lines = tags[tag]
      if len(lines) != 1:
        raise Exception('tag %s is ambiguous (lines: %s)' % (tag, lines))

      self.SetBreakpointAtPathLine(path, lines[0], template)

    def SetBreakpointAtPathLine(self, path, line, template=None):
      """Sets a new breakpoint at path:line."""
      breakpoint = {
          'id': 'BP_%d' % next(self._id_counter),
          'createTime': python_test_util.DateTimeToTimestamp(datetime.utcnow()),
          'location': {
              'path': path,
              'line': line
          }
      }
      breakpoint.update(template or {})

      self.SetActiveBreakpoints(self.GetActiveBreakpoints() + [breakpoint])

    def GetActiveBreakpoints(self):
      """Returns current list of active breakpoints."""
      return self._active_breakpoints['breakpoints']

    def SetActiveBreakpoints(self, breakpoints):
      """Sets a new list of active breakpoints.

      Args:
        breakpoints: list of breakpoints to return to the debuglet.
      """
      self._active_breakpoints['breakpoints'] = breakpoints
      begin_count = self._list_execute.call_count
      while self._list_execute.call_count < begin_count + 2:
        time.sleep(_REQUEST_DELAY_SECS)

    def GetNextResult(self):
      """Waits for the next breakpoint update from the debuglet.

      Returns:
        First breakpoint update sent by the debuglet that hasn't been
        processed yet.

      Raises:
        queue.Empty: if waiting for breakpoint update times out.
      """
      try:
        return self._incoming_breakpoint_updates.get(True, 15)
      except queue.Empty:
        raise AssertionError('Timed out waiting for breakpoint update')

    def TryGetNextResult(self):
      """Returns the first unprocessed breakpoint update from the debuglet.

      Returns:
        First breakpoint update sent by the debuglet that hasn't been
        processed yet. If no updates are pending, returns None.
      """
      try:
        return self._incoming_breakpoint_updates.get_nowait()
      except queue.Empty:
        return None

    def _UpdateBreakpoint(self, **keywords):
      """Fake implementation of service.debuggees().breakpoints().update()."""

      class FakeBreakpointUpdateCommand(object):

        def __init__(self, q):
          self._breakpoint = keywords['body']['breakpoint']
          self._queue = q

        def execute(self):  # pylint: disable=invalid-name
          self._queue.put(self._breakpoint)

      return FakeBreakpointUpdateCommand(self._incoming_breakpoint_updates)


# We only need to attach the debugger exactly once. The IntegrationTest class
# is created for each test case, so we need to keep this state global.

  _hub = FakeHub()

  def _FakeLog(self, message, extra=None):
    del extra  # unused
    self._info_log.append(message)

  def setUp(self):
    self._info_log = []
    capture_collector.log_info_message = self._FakeLog

  def tearDown(self):
    IntegrationTest._hub.SetActiveBreakpoints([])

    while True:
      breakpoint = IntegrationTest._hub.TryGetNextResult()
      if breakpoint is None:
        break
      self.fail('Unexpected incoming breakpoint update: %s' % breakpoint)

  def testBackCompat(self):
    # Verify that the old AttachDebugger() is the same as enable()
    self.assertEqual(cdbg.enable, cdbg.AttachDebugger)

  def testBasic(self):

    def Trigger():
      print('Breakpoint trigger')  # BPTAG: BASIC

    IntegrationTest._hub.SetBreakpoint('BASIC')
    Trigger()
    result = IntegrationTest._hub.GetNextResult()
    self.assertEqual('Trigger', result['stackFrames'][0]['function'])
    self.assertEqual('IntegrationTest.testBasic',
                     result['stackFrames'][1]['function'])

  # Verify that any pre existing labels present in the breakpoint are preserved
  # by the agent.
  def testExistingLabelsSurvive(self):

    def Trigger():
      print('Breakpoint trigger with labels')  # BPTAG: EXISTING_LABELS_SURVIVE

    IntegrationTest._hub.SetBreakpoint(
        'EXISTING_LABELS_SURVIVE',
        {'labels': {
            'label_1': 'value_1',
            'label_2': 'value_2'
        }})
    Trigger()
    result = IntegrationTest._hub.GetNextResult()
    self.assertIn('labels', result.keys())
    self.assertIn('label_1', result['labels'])
    self.assertIn('label_2', result['labels'])
    self.assertEqual('value_1', result['labels']['label_1'])
    self.assertEqual('value_2', result['labels']['label_2'])

  # Verify that any pre existing labels present in the breakpoint have priority
  # if they 'collide' with labels in the agent.
  def testExistingLabelsPriority(self):

    def Trigger():
      print('Breakpoint trigger with labels')  # BPTAG: EXISTING_LABELS_PRIORITY

    current_labels_collector = capture_collector.breakpoint_labels_collector
    capture_collector.breakpoint_labels_collector = \
        lambda: {'label_1': 'value_1', 'label_2': 'value_2'}

    IntegrationTest._hub.SetBreakpoint(
        'EXISTING_LABELS_PRIORITY',
        {'labels': {
            'label_1': 'value_foobar',
            'label_3': 'value_3'
        }})

    Trigger()

    capture_collector.breakpoint_labels_collector = current_labels_collector

    # In this case, label_1 was in both the agent and the pre existing labels,
    # the pre existing value of value_foobar should be preserved.
    result = IntegrationTest._hub.GetNextResult()
    self.assertIn('labels', result.keys())
    self.assertIn('label_1', result['labels'])
    self.assertIn('label_2', result['labels'])
    self.assertIn('label_3', result['labels'])
    self.assertEqual('value_foobar', result['labels']['label_1'])
    self.assertEqual('value_2', result['labels']['label_2'])
    self.assertEqual('value_3', result['labels']['label_3'])

  def testRequestLogIdLabel(self):

    def Trigger():
      print('Breakpoint trigger req id label')  # BPTAG: REQUEST_LOG_ID_LABEL

    current_request_log_id_collector = \
      capture_collector.request_log_id_collector
    capture_collector.request_log_id_collector = lambda: 'foo_bar_id'

    IntegrationTest._hub.SetBreakpoint('REQUEST_LOG_ID_LABEL')

    Trigger()

    capture_collector.request_log_id_collector = \
        current_request_log_id_collector

    result = IntegrationTest._hub.GetNextResult()
    self.assertIn('labels', result.keys())
    self.assertIn(labels.Breakpoint.REQUEST_LOG_ID, result['labels'])
    self.assertEqual('foo_bar_id',
                     result['labels'][labels.Breakpoint.REQUEST_LOG_ID])

  # Tests the issue in b/30876465
  def testSameLine(self):

    def Trigger():
      print('Breakpoint trigger same line')  # BPTAG: SAME_LINE

    num_breakpoints = 5
    _, line = python_test_util.ResolveTag(sys.modules[__name__], 'SAME_LINE')
    for _ in range(0, num_breakpoints):
      IntegrationTest._hub.SetBreakpoint('SAME_LINE')
    Trigger()
    results = []
    for _ in range(0, num_breakpoints):
      results.append(IntegrationTest._hub.GetNextResult())
    lines = [result['stackFrames'][0]['location']['line'] for result in results]
    self.assertListEqual(lines, [line] * num_breakpoints)

  def testCallStack(self):

    def Method1():
      Method2()

    def Method2():
      Method3()

    def Method3():
      Method4()

    def Method4():
      Method5()

    def Method5():
      return 0  # BPTAG: CALL_STACK

    IntegrationTest._hub.SetBreakpoint('CALL_STACK')
    Method1()
    result = IntegrationTest._hub.GetNextResult()
    self.assertEqual([
        'Method5', 'Method4', 'Method3', 'Method2', 'Method1',
        'IntegrationTest.testCallStack'
    ], [frame['function'] for frame in result['stackFrames']][:6])

  def testInnerMethod(self):

    def Inner1():

      def Inner2():

        def Inner3():
          print('Inner3')  # BPTAG: INNER3

        Inner3()

      Inner2()

    IntegrationTest._hub.SetBreakpoint('INNER3')
    Inner1()
    result = IntegrationTest._hub.GetNextResult()
    self.assertEqual('Inner3', result['stackFrames'][0]['function'])

  def testClassMethodWithDecorator(self):

    def MyDecorator(handler):

      def Caller(self):
        return handler(self)

      return Caller

    class BaseClass(object):
      pass

    class MyClass(BaseClass):

      @MyDecorator
      def Get(self):
        param = {}  # BPTAG: METHOD_WITH_DECORATOR
        return str(param)

    IntegrationTest._hub.SetBreakpoint('METHOD_WITH_DECORATOR')
    self.assertEqual('{}', MyClass().Get())
    result = IntegrationTest._hub.GetNextResult()
    self.assertEqual('MyClass.Get', result['stackFrames'][0]['function'])
    self.assertEqual('MyClass.Caller', result['stackFrames'][1]['function'])
    self.assertEqual(
        {
            'name':
                'self',
            'type':
                __name__ + '.MyClass',
            'members': [{
                'status': {
                    'refersTo': 'VARIABLE_NAME',
                    'description': {
                        'format': 'Object has no fields'
                    }
                }
            }]
        },
        python_test_util.PackFrameVariable(
            result, 'self', collection='arguments'))

  def testGlobalDecorator(self):
    IntegrationTest._hub.SetBreakpoint('WRAPPED_GLOBAL_METHOD')
    self.assertEqual('hello', WrappedGlobalMethod())
    result = IntegrationTest._hub.GetNextResult()

    self.assertNotIn('status', result)

  def testNoLambdaExpression(self):

    def Trigger():
      cube = lambda x: x**3  # BPTAG: LAMBDA
      cube(18)

    num_breakpoints = 5
    for _ in range(0, num_breakpoints):
      IntegrationTest._hub.SetBreakpoint('LAMBDA')
    Trigger()
    results = []
    for _ in range(0, num_breakpoints):
      results.append(IntegrationTest._hub.GetNextResult())
    functions = [result['stackFrames'][0]['function'] for result in results]
    self.assertListEqual(functions, ['Trigger'] * num_breakpoints)

  def testNoGeneratorExpression(self):

    def Trigger():
      gen = (i for i in range(0, 5))  # BPTAG: GENEXPR
      next(gen)
      next(gen)
      next(gen)
      next(gen)
      next(gen)

    num_breakpoints = 1
    for _ in range(0, num_breakpoints):
      IntegrationTest._hub.SetBreakpoint('GENEXPR')
    Trigger()
    results = []
    for _ in range(0, num_breakpoints):
      results.append(IntegrationTest._hub.GetNextResult())
    functions = [result['stackFrames'][0]['function'] for result in results]
    self.assertListEqual(functions, ['Trigger'] * num_breakpoints)

  def testTryBlock(self):

    def Method(a):
      try:
        return a * a  # BPTAG: TRY_BLOCK
      except Exception as unused_e:  # pylint: disable=broad-except
        return a

    IntegrationTest._hub.SetBreakpoint('TRY_BLOCK')
    Method(11)
    result = IntegrationTest._hub.GetNextResult()
    self.assertEqual('Method', result['stackFrames'][0]['function'])
    self.assertEqual([{
        'name': 'a',
        'value': '11',
        'type': 'int'
    }], result['stackFrames'][0]['arguments'])

  def testFrameArguments(self):

    def Method(a, b):
      return a + str(b)  # BPTAG: FRAME_ARGUMENTS

    IntegrationTest._hub.SetBreakpoint('FRAME_ARGUMENTS')
    Method('hello', 87)
    result = IntegrationTest._hub.GetNextResult()
    self.assertEqual([{
        'name': 'a',
        'value': "'hello'",
        'type': 'str'
    }, {
        'name': 'b',
        'value': '87',
        'type': 'int'
    }], result['stackFrames'][0]['arguments'])
    self.assertEqual('self', result['stackFrames'][1]['arguments'][0]['name'])

  def testFrameLocals(self):

    class Number(object):

      def __init__(self):
        self.n = 57

    def Method(a):
      b = a**2
      c = str(a) * 3
      return c + str(b)  # BPTAG: FRAME_LOCALS

    IntegrationTest._hub.SetBreakpoint('FRAME_LOCALS')
    x = {'a': 1, 'b': Number()}
    Method(8)
    result = IntegrationTest._hub.GetNextResult()
    self.assertEqual({
        'name': 'b',
        'value': '64',
        'type': 'int'
    }, python_test_util.PackFrameVariable(result, 'b'))
    self.assertEqual({
        'name': 'c',
        'value': "'888'",
        'type': 'str'
    }, python_test_util.PackFrameVariable(result, 'c'))
    self.assertEqual(
        {
            'name':
                'x',
            'type':
                'dict',
            'members': [{
                'name': "'a'",
                'value': '1',
                'type': 'int'
            }, {
                'name': "'b'",
                'type': __name__ + '.Number',
                'members': [{
                    'name': 'n',
                    'value': '57',
                    'type': 'int'
                }]
            }]
        }, python_test_util.PackFrameVariable(result, 'x', frame=1))
    return x

  def testRecursion(self):

    def RecursiveMethod(i):
      if i == 0:
        return 0  # BPTAG: RECURSION
      return RecursiveMethod(i - 1)

    IntegrationTest._hub.SetBreakpoint('RECURSION')
    RecursiveMethod(5)
    result = IntegrationTest._hub.GetNextResult()

    for frame in range(5):
      self.assertEqual({
          'name': 'i',
          'value': str(frame),
          'type': 'int'
      }, python_test_util.PackFrameVariable(result, 'i', frame, 'arguments'))

  def testWatchedExpressions(self):

    def Trigger():

      class MyClass(object):

        def __init__(self):
          self.a = 1
          self.b = 'bbb'

      unused_my = MyClass()
      print('Breakpoint trigger')  # BPTAG: WATCHED_EXPRESSION

    IntegrationTest._hub.SetBreakpoint('WATCHED_EXPRESSION',
                                       {'expressions': ['unused_my']})
    Trigger()
    result = IntegrationTest._hub.GetNextResult()

    self.assertEqual(
        {
            'name':
                'unused_my',
            'type':
                __name__ + '.MyClass',
            'members': [{
                'name': 'a',
                'value': '1',
                'type': 'int'
            }, {
                'name': 'b',
                'value': "'bbb'",
                'type': 'str'
            }]
        }, python_test_util.PackWatchedExpression(result, 0))

  def testBreakpointExpiration(self):  # BPTAG: BREAKPOINT_EXPIRATION
    created_time = datetime.utcnow() - timedelta(hours=25)
    IntegrationTest._hub.SetBreakpoint(
        'BREAKPOINT_EXPIRATION',
        {'createTime': python_test_util.DateTimeToTimestamp(created_time)})
    result = IntegrationTest._hub.GetNextResult()

    self.assertTrue(result['status']['isError'])

  def testLogAction(self):

    def Trigger():
      for i in range(3):
        print('Log me %d' % i)  # BPTAG: LOG

    IntegrationTest._hub.SetBreakpoint(
        'LOG', {
            'action': 'LOG',
            'logLevel': 'INFO',
            'logMessageFormat': 'hello $0',
            'expressions': ['i']
        })
    Trigger()
    self.assertListEqual(
        ['LOGPOINT: hello 0', 'LOGPOINT: hello 1', 'LOGPOINT: hello 2'],
        self._info_log)

  def testDeferred(self):

    def Trigger():
      import integration_test_helper  # pylint: disable=g-import-not-at-top
      integration_test_helper.Trigger()

    IntegrationTest._hub.SetBreakpointAtFile('integration_test_helper.py',
                                             'DEFERRED')

    Trigger()
    result = IntegrationTest._hub.GetNextResult()
    self.assertEqual('Trigger', result['stackFrames'][0]['function'])
    self.assertEqual('Trigger', result['stackFrames'][1]['function'])
    self.assertEqual('IntegrationTest.testDeferred',
                     result['stackFrames'][2]['function'])


def MyGlobalDecorator(fn):

  @functools.wraps(fn)
  def Wrapper(*args, **kwargs):
    return fn(*args, **kwargs)

  return Wrapper


@MyGlobalDecorator
def WrappedGlobalMethod():
  return 'hello'  # BPTAG: WRAPPED_GLOBAL_METHOD


if __name__ == '__main__':
  absltest.main()
