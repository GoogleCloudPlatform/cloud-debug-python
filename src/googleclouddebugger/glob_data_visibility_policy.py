"""Determines the visibilty of python data and symbols.

Example Usage:

  blacklist_patterns = (
    'com.private.*'
    'com.foo.bar'
  )
  whitelist_patterns = (
    'com.*'
  )
  policy = GlobDataVisibilityPolicy(blacklist_patterns, whitelist_patterns)

  policy.IsDataVisible('org.foo.bar') -> (False, 'not whitelisted by config')
  policy.IsDataVisible('com.foo.bar') -> (False, 'blacklisted by config')
  policy.IsDataVisible('com.private.foo') -> (False, 'blacklisted by config')
  policy.IsDataVisible('com.foo') -> (True, 'visible')
"""

import fnmatch


# Possible visibility responses
RESPONSES = {
    'UNKNOWN_TYPE': 'could not determine type',
    'BLACKLISTED': 'blacklisted by config',
    'NOT_WHITELISTED': 'not whitelisted by config',
    'VISIBLE': 'visible',
}


class GlobDataVisibilityPolicy(object):
  """Policy provides visibility policy details to the caller."""

  def __init__(self, blacklist_patterns, whitelist_patterns):
    self.blacklist_patterns = blacklist_patterns
    self.whitelist_patterns = whitelist_patterns

  def IsDataVisible(self, path):
    """Returns a tuple (visible, reason) stating if the data should be visible.

    Args:
      path: A dot separated path that represents a package, class, method or
      variable.  The format is identical to pythons "import" statement.

    Returns:
      (visible, reason) where visible is a boolean that is True if the data
      should be visible.  Reason is a string reason that can be displayed
      to the user and indicates why data is visible or not visible.
    """
    if path is None:
      return (False, RESPONSES['UNKNOWN_TYPE'])

    if _Matches(path, self.blacklist_patterns):
      return (False, RESPONSES['BLACKLISTED'])

    if not _Matches(path, self.whitelist_patterns):
      return (False, RESPONSES['NOT_WHITELISTED'])

    return (True, RESPONSES['VISIBLE'])


def _Matches(path, pattern_list):
  """Returns true if path matches any patten found in pattern_list.

  Args:
    path: A dot separated path to a package, class, method or variable
    pattern_list: A list of wildcard patterns

  Returns:
    True if path matches any wildcard found in pattern_list.
  """
  # TODO(mattwach): This code does not scale to large pattern_list
  # sizes.  For now, keep things logically simple but consider a
  # more optimized solution in the future.
  return any(fnmatch.fnmatchcase(path, pattern) for pattern in pattern_list)

