"""Unit test for module_search2 module."""

import os
import sys
import tempfile

from absl.testing import absltest

from googleclouddebugger import module_search2


# TODO: Add tests for whitespace in location path including in,
# extension, basename, path
class SearchModulesTest(absltest.TestCase):

  def setUp(self):
    self._test_package_dir = tempfile.mkdtemp('', 'package_')
    sys.path.append(self._test_package_dir)

  def tearDown(self):
    sys.path.remove(self._test_package_dir)

  def testSearchValidSourcePath(self):
    # These modules are on the sys.path.
    self.assertEndsWith(
        module_search2.Search(
            'googleclouddebugger/module_search2.py'),
        '/site-packages/googleclouddebugger/module_search2.py')

    # inspect and dis are <embedded stdlib> libraries with no real file. So, we
    # can no longer match them by file path.

  def testSearchInvalidSourcePath(self):
    # This is an invalid module that doesn't exist anywhere.
    self.assertEqual(module_search2.Search('aaaaa.py'), 'aaaaa.py')

    # This module exists, but the search input is missing the outer package
    # name.
    self.assertEqual(
        module_search2.Search('absltest.py'),
        'absltest.py')

  def testSearchInvalidExtension(self):
    # Test that the module rejects invalid extension in the input.
    with self.assertRaises(AssertionError):
      module_search2.Search('module_search2.x')

  def testSearchPathStartsWithSep(self):
    # Test that module rejects invalid leading os.sep char in the input.
    with self.assertRaises(AssertionError):
      module_search2.Search('/module_search2')

  def testSearchRelativeSysPath(self):
    # An entry in sys.path is in relative form, and represents the same
    # directory as as another absolute entry in sys.path.
    for directory in ['', 'a', 'a/b']:
      self._CreateFile(os.path.join(directory, '__init__.py'))
    self._CreateFile('a/b/first.py')

    try:
      # Inject a relative path into sys.path that refers to a directory already
      # in sys.path. It should produce the same result as the non-relative form.
      testdir_alias = os.path.join(self._test_package_dir, 'a/../a')

      # Add 'a/../a' to sys.path so that 'b/first.py' is reachable.
      sys.path.insert(0, testdir_alias)

      # Returned result should have a successful file match and relative
      # paths should be kept as-is.
      result = module_search2.Search('b/first.py')
      self.assertEndsWith(result, 'a/../a/b/first.py')

    finally:
      sys.path.remove(testdir_alias)

  def testSearchSymLinkInSysPath(self):
    # An entry in sys.path is a symlink.
    for directory in ['', 'a', 'a/b']:
      self._CreateFile(os.path.join(directory, '__init__.py'), '')
    self._CreateFile('a/b/first.py')
    self._CreateSymLink('a', 'link')

    try:
      # Add 'link/' to sys.path so that 'b/first.py' is reachable.
      sys.path.append(os.path.join(self._test_package_dir, 'link'))

      # Returned result should have a successful file match and symbolic
      # links should be kept.
      self.assertEndsWith(
          module_search2.Search('b/first.py'),
          'link/b/first.py')
    finally:
      sys.path.remove(os.path.join(self._test_package_dir, 'link'))

  def _CreateFile(self, path, contents='assert False "Unexpected import"\n'):
    full_path = os.path.join(self._test_package_dir, path)
    directory, unused_name = os.path.split(full_path)

    if not os.path.isdir(directory):
      os.makedirs(directory)

    with open(full_path, 'w') as writer:
      writer.write(contents)

    return path

  def _CreateSymLink(self, source, link_name):
    full_source_path = os.path.join(self._test_package_dir, source)
    full_link_path = os.path.join(self._test_package_dir, link_name)
    os.symlink(full_source_path, full_link_path)

  # Since we cannot use os.path.samefile or os.path.realpath to eliminate
  # symlinks reliably, we only check suffix equivalence of file paths in these
  # unit tests.
  def _AssertEndsWith(self, match, path):
    """Asserts exactly one match ending with path."""
    self.assertLen(match, 1)
    self.assertEndsWith(match[0], path)

  def _AssertEqFile(self, match, path):
    """Asserts exactly one match equals to the file created with _CreateFile."""
    self.assertLen(match, 1)
    self.assertEqual(match[0], os.path.join(self._test_package_dir, path))


if __name__ == '__main__':
  absltest.main()
