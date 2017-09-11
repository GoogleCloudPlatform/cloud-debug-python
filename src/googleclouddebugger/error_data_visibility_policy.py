"""Always returns the provided error on visibility requests.

Example Usage:

  policy = ErrorDataVisibilityPolicy('An error message')

  policy.IsDataVisible('org.foo.bar') -> (False, 'An error message')
"""


class ErrorDataVisibilityPolicy(object):
  """Visibility policy that always returns an error to the caller."""

  def __init__(self, error_message):
    self.error_message = error_message

  def IsDataVisible(self, unused_path):
    return (False, self.error_message)
