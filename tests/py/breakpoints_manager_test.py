"""Unit test for breakpoints_manager module."""

from datetime import datetime
from datetime import timedelta
from unittest import mock

from absl.testing import absltest

from googleclouddebugger import breakpoints_manager


class BreakpointsManagerTest(absltest.TestCase):
  """Unit test for breakpoints_manager module."""

  def setUp(self):
    self._breakpoints_manager = breakpoints_manager.BreakpointsManager(
        self, None)

    path = 'googleclouddebugger.breakpoints_manager.'
    breakpoint_class = path + 'python_breakpoint.PythonBreakpoint'

    patcher = mock.patch(breakpoint_class)
    self._mock_breakpoint = patcher.start()
    self.addCleanup(patcher.stop)

  def testEmpty(self):
    self.assertEmpty(self._breakpoints_manager._active)

  def testSetSingle(self):
    self._breakpoints_manager.SetActiveBreakpoints([{'id': 'ID1'}])
    self._mock_breakpoint.assert_has_calls(
        [mock.call({'id': 'ID1'}, self, self._breakpoints_manager, None)])
    self.assertLen(self._breakpoints_manager._active, 1)

  def testSetDouble(self):
    self._breakpoints_manager.SetActiveBreakpoints([{'id': 'ID1'}])
    self._mock_breakpoint.assert_has_calls(
        [mock.call({'id': 'ID1'}, self, self._breakpoints_manager, None)])
    self.assertLen(self._breakpoints_manager._active, 1)

    self._breakpoints_manager.SetActiveBreakpoints([{
        'id': 'ID1'
    }, {
        'id': 'ID2'
    }])
    self._mock_breakpoint.assert_has_calls([
        mock.call({'id': 'ID1'}, self, self._breakpoints_manager, None),
        mock.call({'id': 'ID2'}, self, self._breakpoints_manager, None)
    ])
    self.assertLen(self._breakpoints_manager._active, 2)

  def testSetRepeated(self):
    self._breakpoints_manager.SetActiveBreakpoints([{'id': 'ID1'}])
    self._breakpoints_manager.SetActiveBreakpoints([{'id': 'ID1'}])
    self.assertEqual(1, self._mock_breakpoint.call_count)

  def testClear(self):
    self._breakpoints_manager.SetActiveBreakpoints([{'id': 'ID1'}])
    self._breakpoints_manager.SetActiveBreakpoints([])
    self.assertEqual(1, self._mock_breakpoint.return_value.Clear.call_count)
    self.assertEmpty(self._breakpoints_manager._active)

  def testCompleteInvalidId(self):
    self._breakpoints_manager.CompleteBreakpoint('ID_INVALID')

  def testComplete(self):
    self._breakpoints_manager.SetActiveBreakpoints([{'id': 'ID1'}])
    self._breakpoints_manager.CompleteBreakpoint('ID1')
    self.assertEqual(1, self._mock_breakpoint.return_value.Clear.call_count)

  def testSetCompleted(self):
    self._breakpoints_manager.CompleteBreakpoint('ID1')
    self._breakpoints_manager.SetActiveBreakpoints([{'id': 'ID1'}])
    self.assertEqual(0, self._mock_breakpoint.call_count)

  def testCompletedCleanup(self):
    self._breakpoints_manager.CompleteBreakpoint('ID1')
    self._breakpoints_manager.SetActiveBreakpoints([])
    self._breakpoints_manager.SetActiveBreakpoints([{'id': 'ID1'}])
    self.assertEqual(1, self._mock_breakpoint.call_count)

  def testMultipleSetDelete(self):
    self._breakpoints_manager.SetActiveBreakpoints([{
        'id': 'ID1'
    }, {
        'id': 'ID2'
    }, {
        'id': 'ID3'
    }, {
        'id': 'ID4'
    }])
    self.assertLen(self._breakpoints_manager._active, 4)

    self._breakpoints_manager.SetActiveBreakpoints([{
        'id': 'ID1'
    }, {
        'id': 'ID2'
    }, {
        'id': 'ID3'
    }, {
        'id': 'ID4'
    }])
    self.assertLen(self._breakpoints_manager._active, 4)

    self._breakpoints_manager.SetActiveBreakpoints([])
    self.assertEmpty(self._breakpoints_manager._active)

  def testCombination(self):
    self._breakpoints_manager.SetActiveBreakpoints([{
        'id': 'ID1'
    }, {
        'id': 'ID2'
    }, {
        'id': 'ID3'
    }])
    self.assertLen(self._breakpoints_manager._active, 3)

    self._breakpoints_manager.CompleteBreakpoint('ID2')
    self.assertEqual(1, self._mock_breakpoint.return_value.Clear.call_count)
    self.assertLen(self._breakpoints_manager._active, 2)

    self._breakpoints_manager.SetActiveBreakpoints([{
        'id': 'ID2'
    }, {
        'id': 'ID3'
    }, {
        'id': 'ID4'
    }])
    self.assertEqual(2, self._mock_breakpoint.return_value.Clear.call_count)
    self.assertLen(self._breakpoints_manager._active, 2)

    self._breakpoints_manager.CompleteBreakpoint('ID2')
    self.assertEqual(2, self._mock_breakpoint.return_value.Clear.call_count)
    self.assertLen(self._breakpoints_manager._active, 2)

    self._breakpoints_manager.SetActiveBreakpoints([])
    self.assertEqual(4, self._mock_breakpoint.return_value.Clear.call_count)
    self.assertEmpty(self._breakpoints_manager._active)

  def testCheckExpirationNoBreakpoints(self):
    self._breakpoints_manager.CheckBreakpointsExpiration()

  def testCheckNotExpired(self):
    self._breakpoints_manager.SetActiveBreakpoints([{
        'id': 'ID1'
    }, {
        'id': 'ID2'
    }])
    self._mock_breakpoint.return_value.GetExpirationTime.return_value = (
        datetime.utcnow() + timedelta(minutes=1))
    self._breakpoints_manager.CheckBreakpointsExpiration()
    self.assertEqual(
        0, self._mock_breakpoint.return_value.ExpireBreakpoint.call_count)

  def testCheckExpired(self):
    self._breakpoints_manager.SetActiveBreakpoints([{
        'id': 'ID1'
    }, {
        'id': 'ID2'
    }])
    self._mock_breakpoint.return_value.GetExpirationTime.return_value = (
        datetime.utcnow() - timedelta(minutes=1))
    self._breakpoints_manager.CheckBreakpointsExpiration()
    self.assertEqual(
        2, self._mock_breakpoint.return_value.ExpireBreakpoint.call_count)

  def testCheckExpirationReset(self):
    self._breakpoints_manager.SetActiveBreakpoints([{'id': 'ID1'}])
    self._mock_breakpoint.return_value.GetExpirationTime.return_value = (
        datetime.utcnow() + timedelta(minutes=1))
    self._breakpoints_manager.CheckBreakpointsExpiration()
    self.assertEqual(
        0, self._mock_breakpoint.return_value.ExpireBreakpoint.call_count)

    self._breakpoints_manager.SetActiveBreakpoints([{
        'id': 'ID1'
    }, {
        'id': 'ID2'
    }])
    self._mock_breakpoint.return_value.GetExpirationTime.return_value = (
        datetime.utcnow() - timedelta(minutes=1))
    self._breakpoints_manager.CheckBreakpointsExpiration()
    self.assertEqual(
        2, self._mock_breakpoint.return_value.ExpireBreakpoint.call_count)

  def testCheckExpirationCacheNegative(self):
    base = datetime(2015, 1, 1)

    with mock.patch.object(breakpoints_manager.BreakpointsManager,
                           'GetCurrentTime') as mock_time:
      mock_time.return_value = base

      self._breakpoints_manager.SetActiveBreakpoints([{'id': 'ID1'}])
      self._mock_breakpoint.return_value.GetExpirationTime.return_value = (
          base + timedelta(minutes=1))

      self._breakpoints_manager.CheckBreakpointsExpiration()
      self.assertEqual(
          0, self._mock_breakpoint.return_value.ExpireBreakpoint.call_count)

      # The nearest expiration time is cached, so this should have no effect.
      self._mock_breakpoint.return_value.GetExpirationTime.return_value = (
          base - timedelta(minutes=1))
      self._breakpoints_manager.CheckBreakpointsExpiration()
      self.assertEqual(
          0, self._mock_breakpoint.return_value.ExpireBreakpoint.call_count)

  def testCheckExpirationCachePositive(self):
    base = datetime(2015, 1, 1)

    with mock.patch.object(breakpoints_manager.BreakpointsManager,
                           'GetCurrentTime') as mock_time:
      self._breakpoints_manager.SetActiveBreakpoints([{'id': 'ID1'}])
      self._mock_breakpoint.return_value.GetExpirationTime.return_value = (
          base + timedelta(minutes=1))

      mock_time.return_value = base
      self._breakpoints_manager.CheckBreakpointsExpiration()
      self.assertEqual(
          0, self._mock_breakpoint.return_value.ExpireBreakpoint.call_count)

      mock_time.return_value = base + timedelta(minutes=2)
      self._breakpoints_manager.CheckBreakpointsExpiration()
      self.assertEqual(
          1, self._mock_breakpoint.return_value.ExpireBreakpoint.call_count)


if __name__ == '__main__':
  absltest.main()
