"""Unit test for backoff module."""

from absl.testing import absltest

from googleclouddebugger import backoff


class BackoffTest(absltest.TestCase):
  """Unit test for backoff module."""

  def setUp(self):
    self._backoff = backoff.Backoff(10, 100, 1.5)

  def testInitial(self):
    self.assertEqual(10, self._backoff.Failed())

  def testIncrease(self):
    self._backoff.Failed()
    self.assertEqual(15, self._backoff.Failed())

  def testMaximum(self):
    for _ in range(100):
      self._backoff.Failed()

    self.assertEqual(100, self._backoff.Failed())

  def testResetOnSuccess(self):
    for _ in range(4):
      self._backoff.Failed()
    self._backoff.Succeeded()
    self.assertEqual(10, self._backoff.Failed())


if __name__ == '__main__':
  absltest.main()
