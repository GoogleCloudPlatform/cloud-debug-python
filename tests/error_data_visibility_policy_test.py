"""Tests for googleclouddebugger.error_data_visibility_policy."""

from absl.testing import absltest
from googleclouddebugger import error_data_visibility_policy


class ErrorDataVisibilityPolicyTest(absltest.TestCase):

  def testIsDataVisible(self):
    policy = error_data_visibility_policy.ErrorDataVisibilityPolicy(
        'An error message.')

    self.assertEqual((False, 'An error message.'), policy.IsDataVisible('foo'))


if __name__ == '__main__':
  absltest.main()
