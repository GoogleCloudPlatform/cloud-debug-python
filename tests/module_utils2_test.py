"""Tests for googleclouddebugger.module_utils2."""

import os
import sys
import tempfile

from absl.testing import absltest

from googleclouddebugger import module_utils2


class TestModule(object):
  """Dummy class with __name__ and __file__ attributes."""

  def __init__(self, name, path):
    self.__name__ = name
    self.__file__ = path


def _AddSysModule(name, path):
  sys.modules[name] = TestModule(name, path)


class ModuleUtilsTest(absltest.TestCase):

  def setUp(self):
    self._test_package_dir = tempfile.mkdtemp('', 'package_')
    self.modules = sys.modules.copy()

  def tearDown(self):
    sys.modules = self.modules
    self.modules = None

  def _CreateFile(self, path):
    full_path = os.path.join(self._test_package_dir, path)
    directory, unused_name = os.path.split(full_path)

    if not os.path.isdir(directory):
      os.makedirs(directory)

    with open(full_path, 'w') as writer:
      writer.write('')

    return full_path

  def _CreateSymLink(self, source, link_name):
    full_source_path = os.path.join(self._test_package_dir, source)
    full_link_path = os.path.join(self._test_package_dir, link_name)
    os.symlink(full_source_path, full_link_path)
    return full_link_path

  def _AssertEndsWith(self, a, b, msg=None):
    """Assert that string a ends with string b."""
    if not a.endswith(b):
      standard_msg = '%s does not end with %s' % (a, b)
      self.fail(self._formatMessage(msg, standard_msg))

  def testSimpleLoadedModuleFromSuffix(self):
    # Lookup simple module.
    _AddSysModule('m1', '/a/b/p1/m1.pyc')
    for suffix in [
        'm1.py',
        'm1.pyc',
        'm1.pyo',
        'p1/m1.py',
        'b/p1/m1.py',
        'a/b/p1/m1.py',
        '/a/b/p1/m1.py']:
      m1 = module_utils2.GetLoadedModuleBySuffix(suffix)
      self.assertTrue(m1, 'Module not found')
      self.assertEqual('/a/b/p1/m1.pyc', m1.__file__)

    # Lookup simple package, no ext.
    _AddSysModule('p1', '/a/b/p1/__init__.pyc')
    for suffix in [
        'p1/__init__.py',
        'b/p1/__init__.py',
        'a/b/p1/__init__.py',
        '/a/b/p1/__init__.py']:
      p1 = module_utils2.GetLoadedModuleBySuffix(suffix)
      self.assertTrue(p1, 'Package not found')
      self.assertEqual('/a/b/p1/__init__.pyc', p1.__file__)

    # Lookup via bad suffix.
    for suffix in [
        'm2.py',
        'p2/m1.py',
        'b2/p1/m1.py',
        'a2/b/p1/m1.py',
        '/a2/b/p1/m1.py']:
      m1 = module_utils2.GetLoadedModuleBySuffix(suffix)
      self.assertFalse(m1, 'Module found unexpectedly')

  def testComplexLoadedModuleFromSuffix(self):
    # Lookup complex module.
    _AddSysModule('b.p1.m1', '/a/b/p1/m1.pyc')
    for suffix in [
        'm1.py',
        'p1/m1.py',
        'b/p1/m1.py',
        'a/b/p1/m1.py',
        '/a/b/p1/m1.py']:
      m1 = module_utils2.GetLoadedModuleBySuffix(suffix)
      self.assertTrue(m1, 'Module not found')
      self.assertEqual('/a/b/p1/m1.pyc', m1.__file__)

    # Lookup complex package, no ext.
    _AddSysModule('a.b.p1', '/a/b/p1/__init__.pyc')
    for suffix in [
        'p1/__init__.py',
        'b/p1/__init__.py',
        'a/b/p1/__init__.py',
        '/a/b/p1/__init__.py']:
      p1 = module_utils2.GetLoadedModuleBySuffix(suffix)
      self.assertTrue(p1, 'Package not found')
      self.assertEqual('/a/b/p1/__init__.pyc', p1.__file__)

  def testSimilarLoadedModuleFromSuffix(self):
    # Lookup similar module, no ext.
    _AddSysModule('m1', '/a/b/p2/m1.pyc')
    _AddSysModule('p1.m1', '/a/b1/p1/m1.pyc')
    _AddSysModule('b.p1.m1', '/a1/b/p1/m1.pyc')
    _AddSysModule('a.b.p1.m1', '/a/b/p1/m1.pyc')

    m1 = module_utils2.GetLoadedModuleBySuffix('/a/b/p1/m1.py')
    self.assertTrue(m1, 'Module not found')
    self.assertEqual('/a/b/p1/m1.pyc', m1.__file__)

    # Lookup similar package, no ext.
    _AddSysModule('p1', '/a1/b1/p1/__init__.pyc')
    _AddSysModule('b.p1', '/a1/b/p1/__init__.pyc')
    _AddSysModule('a.b.p1', '/a/b/p1/__init__.pyc')
    p1 = module_utils2.GetLoadedModuleBySuffix('/a/b/p1/__init__.py')
    self.assertTrue(p1, 'Package not found')
    self.assertEqual('/a/b/p1/__init__.pyc', p1.__file__)

  def testDuplicateLoadedModuleFromSuffix(self):
    # Lookup name dup module and package.
    _AddSysModule('m1', '/m1/__init__.pyc')
    _AddSysModule('m1.m1', '/m1/m1.pyc')
    _AddSysModule('m1.m1.m1', '/m1/m1/m1/__init__.pyc')
    _AddSysModule('m1.m1.m1.m1', '/m1/m1/m1/m1.pyc')

    # Ambiguous request, multiple modules might have matched.
    m1 = module_utils2.GetLoadedModuleBySuffix('/m1/__init__.py')
    self.assertTrue(m1, 'Package not found')
    self.assertIn(
        m1.__file__,
        ['/m1/__init__.pyc', '/m1/m1/m1/__init__.pyc'])

    # Ambiguous request, multiple modules might have matched.
    m1m1 = module_utils2.GetLoadedModuleBySuffix('/m1/m1.py')
    self.assertTrue(m1m1, 'Module not found')
    self.assertIn(
        m1m1.__file__,
        ['/m1/m1.pyc', '/m1/m1/m1/m1.pyc'])

    # Not ambiguous. Only 1 match possible.
    m1m1m1 = module_utils2.GetLoadedModuleBySuffix('/m1/m1/m1/__init__.py')
    self.assertTrue(m1m1m1, 'Package not found')
    self.assertEqual('/m1/m1/m1/__init__.pyc', m1m1m1.__file__)

    # Not ambiguous. Only 1 match possible.
    m1m1m1m1 = module_utils2.GetLoadedModuleBySuffix('/m1/m1/m1/m1.py')
    self.assertTrue(m1m1m1m1, 'Module not found')
    self.assertEqual('/m1/m1/m1/m1.pyc', m1m1m1m1.__file__)

  def testMainLoadedModuleFromSuffix(self):
    # Lookup complex module.
    _AddSysModule('__main__', '/a/b/p/m.pyc')
    m1 = module_utils2.GetLoadedModuleBySuffix('/a/b/p/m.py')
    self.assertTrue(m1, 'Module not found')
    self.assertEqual('/a/b/p/m.pyc', m1.__file__)


if __name__ == '__main__':
  absltest.main()
