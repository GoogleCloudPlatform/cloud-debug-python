"""Unit tests for native module."""

import inspect
import sys
import threading
import time

import six

from absl.testing import absltest

from googleclouddebugger import cdbg_native as native
import python_test_util


def _DoHardWork(base):
  for i in range(base):
    if base * i < 0:
      return True
  return False


class NativeModuleTest(absltest.TestCase):
  """Unit tests for native module."""

  def setUp(self):
    # Lock for thread safety.
    self._lock = threading.Lock()

    # Count hit count for the breakpoints we set.
    self._breakpoint_counter = 0

    # Registers breakpoint events other than breakpoint hit.
    self._breakpoint_events = []

    # Keep track of breakpoints we set to reset them on cleanup.
    self._cookies = []

  def tearDown(self):
    # Verify that we didn't get any breakpoint events that the test did
    # not expect.
    self.assertEqual([], self._PopBreakpointEvents())

    self._ClearAllBreakpoints()

  def testUnconditionalBreakpoint(self):
    def Trigger():
      unused_lock = threading.Lock()
      print('Breakpoint trigger')  # BPTAG: UNCONDITIONAL_BREAKPOINT

    self._SetBreakpoint(Trigger, 'UNCONDITIONAL_BREAKPOINT')
    Trigger()
    self.assertEqual(1, self._breakpoint_counter)

  def testConditionalBreakpoint(self):
    def Trigger():
      d = {}
      for i in range(1, 10):
        d[i] = i**2  # BPTAG: CONDITIONAL_BREAKPOINT

    self._SetBreakpoint(Trigger, 'CONDITIONAL_BREAKPOINT', 'i % 3 == 1')
    Trigger()
    self.assertEqual(3, self._breakpoint_counter)

  def testClearBreakpoint(self):
    """Set two breakpoint on the same line, then clear one."""

    def Trigger():
      print('Breakpoint trigger')  # BPTAG: CLEAR_BREAKPOINT

    self._SetBreakpoint(Trigger, 'CLEAR_BREAKPOINT')
    self._SetBreakpoint(Trigger, 'CLEAR_BREAKPOINT')
    native.ClearConditionalBreakpoint(self._cookies.pop())
    Trigger()
    self.assertEqual(1, self._breakpoint_counter)

  def testMissingModule(self):
    def Test():
      native.CreateConditionalBreakpoint(None, 123123, None,
                                         self._BreakpointEvent)

    self.assertRaises(TypeError, Test)

  def testBadModule(self):
    def Test():
      native.CreateConditionalBreakpoint('str', 123123, None,
                                         self._BreakpointEvent)

    self.assertRaises(TypeError, Test)

  def testInvalidCondition(self):
    def Test():
      native.CreateConditionalBreakpoint(sys.modules[__name__], 123123, '2+2',
                                         self._BreakpointEvent)

    self.assertRaises(TypeError, Test)

  def testMissingCallback(self):
    def Test():
      native.CreateConditionalBreakpoint('code.py', 123123, None, None)

    self.assertRaises(TypeError, Test)

  def testInvalidCallback(self):
    def Test():
      native.CreateConditionalBreakpoint('code.py', 123123, None, {})

    self.assertRaises(TypeError, Test)

  def testMissingCookie(self):
    self.assertRaises(
        TypeError,
        lambda: native.ClearConditionalBreakpoint(None))

  def testInvalidCookie(self):
    native.ClearConditionalBreakpoint(387873457)

  def testMutableCondition(self):
    def Trigger():
      def MutableMethod():
        self._evil = True
        return True
      print('MutableMethod = %s' % MutableMethod)  # BPTAG: MUTABLE_CONDITION

    self._SetBreakpoint(Trigger, 'MUTABLE_CONDITION', 'MutableMethod()')
    Trigger()
    self.assertEqual(
        [native.BREAKPOINT_EVENT_CONDITION_EXPRESSION_MUTABLE],
        self._PopBreakpointEvents())

  def testGlobalConditionQuotaExceeded(self):
    def Trigger():
      print('Breakpoint trigger')  # BPTAG: GLOBAL_CONDITION_QUOTA

    self._SetBreakpoint(Trigger, 'GLOBAL_CONDITION_QUOTA', '_DoHardWork(1000)')
    Trigger()
    self._ClearAllBreakpoints()

    self.assertListEqual(
        [native.BREAKPOINT_EVENT_GLOBAL_CONDITION_QUOTA_EXCEEDED],
        self._PopBreakpointEvents())

    # Sleep for some time to let the quota recover.
    time.sleep(0.1)

  def testBreakpointConditionQuotaExceeded(self):
    def Trigger():
      print('Breakpoint trigger')  # BPTAG: PER_BREAKPOINT_CONDITION_QUOTA

    time.sleep(1)

    # Per-breakpoint quota is lower than the global one. Exponentially
    # increase the complexity of a condition until we hit it.
    base = 100
    while True:
      self._SetBreakpoint(
          Trigger,
          'PER_BREAKPOINT_CONDITION_QUOTA',
          '_DoHardWork(%d)' % base)
      Trigger()
      self._ClearAllBreakpoints()

      events = self._PopBreakpointEvents()
      if events:
        self.assertEqual(
            [native.BREAKPOINT_EVENT_BREAKPOINT_CONDITION_QUOTA_EXCEEDED],
            events)
        break

      base *= 1.2
      time.sleep(0.1)

    # Sleep for some time to let the quota recover.
    time.sleep(0.1)

  def testImmutableCallSuccess(self):
    def Add(a, b, c):
      return a + b + c

    def Magic():
      return 'cake'

    self.assertEqual(
        '643535',
        self._CallImmutable(inspect.currentframe(), 'str(643535)'))
    self.assertEqual(
        786 + 23 + 891,
        self._CallImmutable(inspect.currentframe(), 'Add(786, 23, 891)'))
    self.assertEqual(
        'cake',
        self._CallImmutable(inspect.currentframe(), 'Magic()'))
    return Add or Magic

  def testImmutableCallMutable(self):
    def Change():
      dictionary['bad'] = True

    dictionary = {}
    frame = inspect.currentframe()
    self.assertRaises(
        SystemError,
        lambda: self._CallImmutable(frame, 'Change()'))
    self.assertEqual({}, dictionary)
    return Change

  def testImmutableCallExceptionPropagation(self):
    def Divide(a, b):
      return a / b

    frame = inspect.currentframe()
    self.assertRaises(
        ZeroDivisionError,
        lambda: self._CallImmutable(frame, 'Divide(1, 0)'))
    return Divide

  def testImmutableCallInvalidFrame(self):
    self.assertRaises(
        TypeError,
        lambda: native.CallImmutable(None, lambda: 1))
    self.assertRaises(
        TypeError,
        lambda: native.CallImmutable('not a frame', lambda: 1))

  def testImmutableCallInvalidCallable(self):
    frame = inspect.currentframe()
    self.assertRaises(
        TypeError,
        lambda: native.CallImmutable(frame, None))
    self.assertRaises(
        TypeError,
        lambda: native.CallImmutable(frame, 'not a callable'))

  def _SetBreakpoint(self, method, tag, condition=None):
    """Sets a breakpoint in this source file.

    The line number is identified by tag. This function does not verify that
    the source line is in the specified method.

    The breakpoint may have an optional condition.

    Args:
      method: method in which the breakpoint will be set.
      tag: label for a source line.
      condition: optional breakpoint condition.
    """
    unused_path, line = python_test_util.ResolveTag(type(self), tag)

    compiled_condition = None
    if condition is not None:
      compiled_condition = compile(condition, '<string>', 'eval')

    cookie = native.CreateConditionalBreakpoint(
        six.get_function_code(method), line, compiled_condition,
        self._BreakpointEvent)

    self._cookies.append(cookie)
    native.ActivateConditionalBreakpoint(cookie)

  def _ClearAllBreakpoints(self):
    """Removes all previously set breakpoints."""
    for cookie in self._cookies:
      native.ClearConditionalBreakpoint(cookie)

  def _CallImmutable(self, frame, expression):
    """Wrapper over native.ImmutableCall for callable."""
    return native.CallImmutable(
        frame,
        compile(expression, '<expression>', 'eval'))

  def _BreakpointEvent(self, event, frame):
    """Callback on breakpoint event.

    See thread_breakpoints.h for more details of possible events.

    Args:
      event: breakpoint event (see kIntegerConstants in native_module.cc).
      frame: Python stack frame of breakpoint hit or None for other events.
    """
    with self._lock:
      if event == native.BREAKPOINT_EVENT_HIT:
        self.assertTrue(inspect.isframe(frame))
        self._breakpoint_counter += 1
      else:
        self._breakpoint_events.append(event)

  def _PopBreakpointEvents(self):
    """Gets and resets the list of breakpoint events received so far."""
    with self._lock:
      events = self._breakpoint_events
      self._breakpoint_events = []
      return events

  def _HasBreakpointEvents(self):
    """Checks whether there are unprocessed breakpoint events."""
    with self._lock:
      if self._breakpoint_events:
        return True
      return False


if __name__ == '__main__':
  absltest.main()
