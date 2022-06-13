"""Tests for glob_data_visibility_policy."""

from absl.testing import absltest
from googleclouddebugger import glob_data_visibility_policy

RESPONSES = glob_data_visibility_policy.RESPONSES
UNKNOWN_TYPE = (False, RESPONSES['UNKNOWN_TYPE'])
BLACKLISTED = (False, RESPONSES['BLACKLISTED'])
NOT_WHITELISTED = (False, RESPONSES['NOT_WHITELISTED'])
VISIBLE = (True, RESPONSES['VISIBLE'])


class GlobDataVisibilityPolicyTest(absltest.TestCase):

  def testIsDataVisible(self):
    blacklist_patterns = (
        'wl1.private1',
        'wl2.*',
        '*.private2',
        '',
    )
    whitelist_patterns = (
        'wl1.*',
        'wl2.*'
    )

    policy = glob_data_visibility_policy.GlobDataVisibilityPolicy(
        blacklist_patterns, whitelist_patterns)

    self.assertEqual(BLACKLISTED, policy.IsDataVisible('wl1.private1'))
    self.assertEqual(BLACKLISTED, policy.IsDataVisible('wl2.foo'))
    self.assertEqual(BLACKLISTED, policy.IsDataVisible('foo.private2'))
    self.assertEqual(NOT_WHITELISTED, policy.IsDataVisible('wl3.foo'))
    self.assertEqual(VISIBLE, policy.IsDataVisible('wl1.foo'))
    self.assertEqual(UNKNOWN_TYPE, policy.IsDataVisible(None))


if __name__ == '__main__':
  absltest.main()
