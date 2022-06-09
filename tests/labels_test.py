"""Tests for googleclouddebugger.labels"""

from absl.testing import absltest
from googleclouddebugger import labels


class LabelsTest(absltest.TestCase):

  def testDefinesLabelsCorrectly(self):
    self.assertEqual(labels.Breakpoint.REQUEST_LOG_ID, 'requestlogid')

    self.assertEqual(labels.Debuggee.DOMAIN, 'domain')
    self.assertEqual(labels.Debuggee.PROJECT_ID, 'projectid')
    self.assertEqual(labels.Debuggee.MODULE, 'module')
    self.assertEqual(labels.Debuggee.VERSION, 'version')
    self.assertEqual(labels.Debuggee.MINOR_VERSION, 'minorversion')
    self.assertEqual(labels.Debuggee.PLATFORM, 'platform')
    self.assertEqual(labels.Debuggee.REGION, 'region')

  def testProvidesAllLabelsSet(self):
    self.assertIsNotNone(labels.Breakpoint.SET_ALL)
    self.assertLen(labels.Breakpoint.SET_ALL, 1)

    self.assertIsNotNone(labels.Debuggee.SET_ALL)
    self.assertLen(labels.Debuggee.SET_ALL, 7)


if __name__ == '__main__':
  absltest.main()
