"""Tests for yaml_data_visibility_config_reader."""

import os
import sys
from unittest import mock

from io import StringIO

from absl.testing import absltest
from googleclouddebugger import yaml_data_visibility_config_reader


class StringIOOpen(object):
  """An open for StringIO that supports "with" semantics.

  I tried using mock.mock_open, but the read logic in the yaml.load code is
  incompatible with the returned mock object, leading to a test hang/timeout.
  """

  def __init__(self, data):
    self.file_obj = StringIO(data)

  def __enter__(self):
    return self.file_obj

  def __exit__(self, type, value, traceback):  # pylint: disable=redefined-builtin
    pass


class YamlDataVisibilityConfigReaderTest(absltest.TestCase):

  def testOpenAndReadSuccess(self):
    data = """
      blacklist:
        - bl1
    """
    path_prefix = 'googleclouddebugger.'
    with mock.patch(path_prefix + 'yaml_data_visibility_config_reader.open',
                    create=True) as m:
      m.return_value = StringIOOpen(data)
      config = yaml_data_visibility_config_reader.OpenAndRead()
      m.assert_called_with(os.path.join(sys.path[0], 'debugger-blacklist.yaml'),
                           'r')
      self.assertEqual(config.blacklist_patterns, ['bl1'])

  def testOpenAndReadFileNotFound(self):
    path_prefix = 'googleclouddebugger.'
    with mock.patch(path_prefix + 'yaml_data_visibility_config_reader.open',
                    create=True, side_effect=IOError('IO Error')):
      f = yaml_data_visibility_config_reader.OpenAndRead()
      self.assertIsNone(f)

  def testReadDataSuccess(self):
    data = """
      blacklist:
        - bl1
        - bl2
      whitelist:
        - wl1
        - wl2.*
    """

    config = yaml_data_visibility_config_reader.Read(StringIO(data))
    self.assertItemsEqual(config.blacklist_patterns, ('bl1', 'bl2'))
    self.assertItemsEqual(config.whitelist_patterns, ('wl1', 'wl2.*'))

  def testYAMLLoadError(self):
    class ErrorIO(object):

      def read(self, size):
        del size  # Unused
        raise IOError('IO Error')

    with self.assertRaises(yaml_data_visibility_config_reader.YAMLLoadError):
      yaml_data_visibility_config_reader.Read(ErrorIO())

  def testBadYamlSyntax(self):
    data = """
      blacklist: whitelist:
    """

    with self.assertRaises(yaml_data_visibility_config_reader.ParseError):
      yaml_data_visibility_config_reader.Read(StringIO(data))

  def testUnknownConfigKeyError(self):
    data = """
      foo:
        - bar
    """

    with self.assertRaises(
        yaml_data_visibility_config_reader.UnknownConfigKeyError):
      yaml_data_visibility_config_reader.Read(StringIO(data))

  def testNotAListError(self):
    data = """
      blacklist:
        foo:
          - bar
    """

    with self.assertRaises(yaml_data_visibility_config_reader.NotAListError):
      yaml_data_visibility_config_reader.Read(StringIO(data))

  def testElementNotAStringError(self):
    data = """
      blacklist:
        - 5
    """

    with self.assertRaises(
        yaml_data_visibility_config_reader.ElementNotAStringError):
      yaml_data_visibility_config_reader.Read(StringIO(data))


if __name__ == '__main__':
  absltest.main()
