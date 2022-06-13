"""Unit test for uniquifier_computer module."""

import os
import sys
import tempfile

from absl.testing import absltest

from googleclouddebugger import uniquifier_computer


class UniquifierComputerTest(absltest.TestCase):

  def _Compute(self, files):
    """Creates a directory structure and computes uniquifier on it.

    Args:
      files: dictionary of relative path to file content.

    Returns:
      Uniquifier data lines.
    """

    class Hash(object):
      """Fake implementation of hash to collect raw data."""

      def __init__(self):
        self.data = b''

      def update(self, s):
        self.data += s

    root = tempfile.mkdtemp('', 'fake_app_')
    for relative_path, content in files.items():
      path = os.path.join(root, relative_path)
      directory = os.path.split(path)[0]
      if not os.path.exists(directory):
        os.makedirs(directory)
      with open(path, 'w') as f:
        f.write(content)

    sys.path.insert(0, root)
    try:
      hash_obj = Hash()
      uniquifier_computer.ComputeApplicationUniquifier(hash_obj)
      return [
          u.decode() for u in (
              hash_obj.data.rstrip(b'\n').split(b'\n') if hash_obj.data else [])
      ]
    finally:
      del sys.path[0]

  def testEmpty(self):
    self.assertListEqual(
        [],
        self._Compute({}))

  def testBundle(self):
    self.assertListEqual(
        ['first.py:1',
         'in1/__init__.py:6',
         'in1/a.py:3',
         'in1/b.py:4',
         'in1/in2/__init__.py:7',
         'in1/in2/c.py:5',
         'second.py:2'],
        self._Compute({
            'db.app': 'abc',
            'first.py': 'a',
            'second.py': 'bb',
            'in1/a.py': 'ccc',
            'in1/b.py': 'dddd',
            'in1/in2/c.py': 'eeeee',
            'in1/__init__.py': 'ffffff',
            'in1/in2/__init__.py': 'ggggggg'}))

  def testEmptyFile(self):
    self.assertListEqual(
        ['empty.py:0'],
        self._Compute({
            'empty.py': ''}))

  def testNonPythonFilesIgnored(self):
    self.assertListEqual(
        ['real.py:1'],
        self._Compute({
            'file.p': '',
            'file.pya': '',
            'real.py': '1'}))

  def testNonPackageDirectoriesIgnored(self):
    self.assertListEqual(
        ['dir2/__init__.py:1'],
        self._Compute({
            'dir1/file.py': '',
            'dir2/__init__.py': 'a',
            'dir2/image.gif': ''}))

  def testDepthLimit(self):
    self.assertListEqual(
        [''.join(str(n) + '/' for n in range(1, m + 1)) + '__init__.py:%d' % m
         for m in range(9, 0, -1)],
        self._Compute({
            '1/__init__.py': '1',
            '1/2/__init__.py': '2' * 2,
            '1/2/3/__init__.py': '3' * 3,
            '1/2/3/4/__init__.py': '4' * 4,
            '1/2/3/4/5/__init__.py': '5' * 5,
            '1/2/3/4/5/6/__init__.py': '6' * 6,
            '1/2/3/4/5/6/7/__init__.py': '7' * 7,
            '1/2/3/4/5/6/7/8/__init__.py': '8' * 8,
            '1/2/3/4/5/6/7/8/9/__init__.py': '9' * 9,
            '1/2/3/4/5/6/7/8/9/10/__init__.py': 'a' * 10,
            '1/2/3/4/5/6/7/8/9/10/11/__init__.py': 'b' * 11}))

  def testPrecedence(self):
    self.assertListEqual(
        ['my.py:3'],
        self._Compute({
            'my.pyo': 'a',
            'my.pyc': 'aa',
            'my.py': 'aaa'}))

if __name__ == '__main__':
  absltest.main()
