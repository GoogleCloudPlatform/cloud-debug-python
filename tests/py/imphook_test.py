"""Unit test for imphook module."""

import importlib
import os
import sys
import tempfile

from absl.testing import absltest

from googleclouddebugger import imphook


class ImportHookTest(absltest.TestCase):
  """Tests for the new module import hook."""

  def setUp(self):
    self._test_package_dir = tempfile.mkdtemp('', 'imphook_')
    sys.path.append(self._test_package_dir)

    self._import_callbacks_log = []
    self._callback_cleanups = []

  def tearDown(self):
    sys.path.remove(self._test_package_dir)

    for cleanup in self._callback_cleanups:
      cleanup()

    # Assert no hooks or entries remained in the set.
    self.assertEmpty(imphook._import_callbacks)

  def testPackageImport(self):
    self._Hook(self._CreateFile('testpkg1/__init__.py'))
    import testpkg1  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(['testpkg1/__init__.py'], self._import_callbacks_log)

  def testModuleImport(self):
    self._CreateFile('testpkg2/__init__.py')
    self._Hook(self._CreateFile('testpkg2/my.py'))
    import testpkg2.my  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(['testpkg2/my.py'], self._import_callbacks_log)

  def testUnrelatedImport(self):
    self._CreateFile('testpkg3/__init__.py')
    self._Hook(self._CreateFile('testpkg3/first.py'))
    self._CreateFile('testpkg3/second.py')
    import testpkg3.second  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEmpty(self._import_callbacks_log)

  def testDoubleImport(self):
    self._Hook(self._CreateFile('testpkg4/__init__.py'))
    import testpkg4  # pylint: disable=g-import-not-at-top,unused-variable
    import testpkg4  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(['testpkg4/__init__.py', 'testpkg4/__init__.py'],
                     sorted(self._import_callbacks_log))

  def testRemoveCallback(self):
    cleanup = self._Hook(self._CreateFile('testpkg4b/__init__.py'))
    cleanup()
    import testpkg4b  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEmpty(self._import_callbacks_log)

  def testRemoveCallbackAfterImport(self):
    cleanup = self._Hook(self._CreateFile('testpkg5/__init__.py'))
    import testpkg5  # pylint: disable=g-import-not-at-top,unused-variable
    cleanup()
    import testpkg5  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(['testpkg5/__init__.py'], self._import_callbacks_log)

  def testTransitiveImport(self):
    self._CreateFile('testpkg6/__init__.py')
    self._Hook(self._CreateFile('testpkg6/first.py', 'import second'))
    self._Hook(self._CreateFile('testpkg6/second.py', 'import third'))
    self._Hook(self._CreateFile('testpkg6/third.py'))
    import testpkg6.first  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(
        ['testpkg6/first.py', 'testpkg6/second.py', 'testpkg6/third.py'],
        sorted(self._import_callbacks_log))

  def testPackageDotModuleImport(self):
    self._Hook(self._CreateFile('testpkg8/__init__.py'))
    self._Hook(self._CreateFile('testpkg8/my.py'))
    import testpkg8.my  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(['testpkg8/__init__.py', 'testpkg8/my.py'],
                     sorted(self._import_callbacks_log))

  def testNestedPackageDotModuleImport(self):
    self._Hook(self._CreateFile('testpkg9a/__init__.py'))
    self._Hook(self._CreateFile('testpkg9a/testpkg9b/__init__.py'))
    self._CreateFile('testpkg9a/testpkg9b/my.py')
    import testpkg9a.testpkg9b.my  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(
        ['testpkg9a/__init__.py', 'testpkg9a/testpkg9b/__init__.py'],
        sorted(self._import_callbacks_log))

  def testFromImport(self):
    self._Hook(self._CreateFile('testpkg10/__init__.py'))
    self._CreateFile('testpkg10/my.py')
    from testpkg10 import my  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(['testpkg10/__init__.py'], self._import_callbacks_log)

  def testTransitiveFromImport(self):
    self._CreateFile('testpkg7/__init__.py')
    self._Hook(
        self._CreateFile('testpkg7/first.py', 'from testpkg7 import second'))
    self._Hook(self._CreateFile('testpkg7/second.py'))
    from testpkg7 import first  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(['testpkg7/first.py', 'testpkg7/second.py'],
                     sorted(self._import_callbacks_log))

  def testFromNestedPackageImportModule(self):
    self._Hook(self._CreateFile('testpkg11a/__init__.py'))
    self._Hook(self._CreateFile('testpkg11a/testpkg11b/__init__.py'))
    self._Hook(self._CreateFile('testpkg11a/testpkg11b/my.py'))
    self._Hook(self._CreateFile('testpkg11a/testpkg11b/your.py'))
    from testpkg11a.testpkg11b import my, your  # pylint: disable=g-import-not-at-top,unused-variable,g-multiple-import
    self.assertEqual([
        'testpkg11a/__init__.py', 'testpkg11a/testpkg11b/__init__.py',
        'testpkg11a/testpkg11b/my.py', 'testpkg11a/testpkg11b/your.py'
    ], sorted(self._import_callbacks_log))

  def testDoubleNestedImport(self):
    self._Hook(self._CreateFile('testpkg12a/__init__.py'))
    self._Hook(self._CreateFile('testpkg12a/testpkg12b/__init__.py'))
    self._Hook(self._CreateFile('testpkg12a/testpkg12b/my.py'))
    from testpkg12a.testpkg12b import my  # pylint: disable=g-import-not-at-top,unused-variable,g-multiple-import
    from testpkg12a.testpkg12b import my  # pylint: disable=g-import-not-at-top,unused-variable,g-multiple-import
    self.assertEqual([
        'testpkg12a/__init__.py', 'testpkg12a/__init__.py',
        'testpkg12a/testpkg12b/__init__.py',
        'testpkg12a/testpkg12b/__init__.py', 'testpkg12a/testpkg12b/my.py',
        'testpkg12a/testpkg12b/my.py'
    ], sorted(self._import_callbacks_log))

  def testFromPackageImportStar(self):
    self._Hook(self._CreateFile('testpkg13a/__init__.py'))
    self._Hook(self._CreateFile('testpkg13a/my1.py'))
    self._Hook(self._CreateFile('testpkg13a/your1.py'))
    # Star imports are only allowed at the top level, not inside a function in
    # Python 3. Doing so would be a SyntaxError.
    exec('from testpkg13a import *')  # pylint: disable=exec-used
    self.assertEqual(['testpkg13a/__init__.py'], self._import_callbacks_log)

  def testFromPackageImportStarWith__all__(self):
    self._Hook(self._CreateFile('testpkg14a/__init__.py', '__all__=["my1"]'))
    self._Hook(self._CreateFile('testpkg14a/my1.py'))
    self._Hook(self._CreateFile('testpkg14a/your1.py'))
    exec('from testpkg14a import *')  # pylint: disable=exec-used
    self.assertEqual(['testpkg14a/__init__.py', 'testpkg14a/my1.py'],
                     sorted(self._import_callbacks_log))

  def testImportFunction(self):
    self._Hook(self._CreateFile('testpkg27/__init__.py'))
    __import__('testpkg27')
    self.assertEqual(['testpkg27/__init__.py'], self._import_callbacks_log)

  def testImportLib(self):
    self._Hook(self._CreateFile('zero.py'))
    self._Hook(self._CreateFile('testpkg15a/__init__.py'))
    self._Hook(self._CreateFile('testpkg15a/first.py'))
    self._Hook(
        self._CreateFile('testpkg15a/testpkg15b/__init__.py',
                         'assert False, "unexpected import"'))
    self._Hook(self._CreateFile('testpkg15a/testpkg15c/__init__.py'))
    self._Hook(self._CreateFile('testpkg15a/testpkg15c/second.py'))

    # Import top level module.
    importlib.import_module('zero')
    self.assertEqual(['zero.py'], self._import_callbacks_log)
    self._import_callbacks_log = []

    # Import top level package.
    importlib.import_module('testpkg15a')
    self.assertEqual(['testpkg15a/__init__.py'], self._import_callbacks_log)
    self._import_callbacks_log = []

    # Import package.module.
    importlib.import_module('testpkg15a.first')
    self.assertEqual(['testpkg15a/__init__.py', 'testpkg15a/first.py'],
                     sorted(self._import_callbacks_log))
    self._import_callbacks_log = []

    # Relative module import from package context.
    importlib.import_module('.first', 'testpkg15a')
    self.assertEqual(['testpkg15a/__init__.py', 'testpkg15a/first.py'],
                     sorted(self._import_callbacks_log))
    self._import_callbacks_log = []

    # Relative module import from package context with '..'.
    # In Python 3, the parent module has to be loaded before a relative import
    importlib.import_module('testpkg15a.testpkg15c')
    self._import_callbacks_log = []
    importlib.import_module('..first', 'testpkg15a.testpkg15c')
    self.assertEqual(
        [
            'testpkg15a/__init__.py',
            # TODO: Importlib may or may not load testpkg15b,
            # depending on the implementation. Currently on blaze, it does not
            # load testpkg15b, but a similar non-blaze code on my workstation
            # loads testpkg15b. We should verify this behavior.
            # 'testpkg15a/testpkg15b/__init__.py',
            'testpkg15a/first.py'
        ],
        sorted(self._import_callbacks_log))
    self._import_callbacks_log = []

    # Relative module import from nested package context.
    importlib.import_module('.second', 'testpkg15a.testpkg15c')
    self.assertEqual([
        'testpkg15a/__init__.py', 'testpkg15a/testpkg15c/__init__.py',
        'testpkg15a/testpkg15c/second.py'
    ], sorted(self._import_callbacks_log))
    self._import_callbacks_log = []

  def testRemoveImportHookFromCallback(self):

    def RunCleanup(unused_mod):
      cleanup()

    cleanup = self._Hook(self._CreateFile('testpkg15/__init__.py'), RunCleanup)
    import testpkg15  # pylint: disable=g-import-not-at-top,unused-variable
    import testpkg15  # pylint: disable=g-import-not-at-top,unused-variable
    import testpkg15  # pylint: disable=g-import-not-at-top,unused-variable

    # The first import should have removed the hook, so expect only one entry.
    self.assertEqual(['testpkg15/__init__.py'], self._import_callbacks_log)

  def testInitImportNoPrematureCallback(self):
    # Verifies that the callback is not invoked before the package is fully
    # loaded. Thus, assuring that the all module code is available for lookup.
    def CheckFullyLoaded(module):
      self.assertEqual(1, getattr(module, 'validate', None), 'premature call')

    self._Hook(self._CreateFile('testpkg16/my1.py'))
    self._Hook(
        self._CreateFile('testpkg16/__init__.py', 'import my1\nvalidate = 1'),
        CheckFullyLoaded)
    import testpkg16.my1  # pylint: disable=g-import-not-at-top,unused-variable

    self.assertEqual(['testpkg16/__init__.py', 'testpkg16/my1.py'],
                     sorted(self._import_callbacks_log))

  def testCircularImportNoPrematureCallback(self):
    # Verifies that the callback is not invoked before the first module is fully
    # loaded. Thus, assuring that the all module code is available for lookup.
    def CheckFullyLoaded(module):
      self.assertEqual(1, getattr(module, 'validate', None), 'premature call')

    self._CreateFile('testpkg17/__init__.py')
    self._Hook(
        self._CreateFile('testpkg17/c1.py', 'import testpkg17.c2\nvalidate = 1',
                         False), CheckFullyLoaded)
    self._Hook(
        self._CreateFile('testpkg17/c2.py', 'import testpkg17.c1\nvalidate = 1',
                         False), CheckFullyLoaded)

    import testpkg17.c1  # pylint: disable=g-import-not-at-top,unused-variable

    self.assertEqual(['testpkg17/c1.py', 'testpkg17/c2.py'],
                     sorted(self._import_callbacks_log))

  def testImportException(self):
    # An exception is thrown by the builtin importer during import.
    self._CreateFile('testpkg18/__init__.py')
    self._Hook(self._CreateFile('testpkg18/bad.py', 'assert False, "bad file"'))
    self._Hook(self._CreateFile('testpkg18/good.py'))

    try:
      import testpkg18.bad  # pylint: disable=g-import-not-at-top,unused-variable
    except AssertionError:
      pass

    import testpkg18.good  # pylint: disable=g-import-not-at-top,unused-variable

    self.assertEqual(['testpkg18/good.py'], self._import_callbacks_log)

  def testImportNestedException(self):
    # An import exception is thrown and caught inside a module being imported.
    self._CreateFile('testpkg19/__init__.py')
    self._Hook(
        self._CreateFile('testpkg19/m19.py',
                         'try: import m19b\nexcept ImportError: pass'))

    import testpkg19.m19  # pylint: disable=g-import-not-at-top,unused-variable

    self.assertEqual(['testpkg19/m19.py'], self._import_callbacks_log)

  def testModuleImportByPathSuffix(self):
    # Import module by providing only a suffix of the module's file path.
    self._CreateFile('testpkg20a/__init__.py')
    self._CreateFile('testpkg20a/testpkg20b/__init__.py')
    self._CreateFile('testpkg20a/testpkg20b/my1.py')
    self._CreateFile('testpkg20a/testpkg20b/my2.py')
    self._CreateFile('testpkg20a/testpkg20b/my3.py')

    # Import just by the name of the module file.
    self._Hook('my1.py')
    import testpkg20a.testpkg20b.my1  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(['my1.py'], self._import_callbacks_log)
    self._import_callbacks_log = []

    # Import with only one of the enclosing package names.
    self._Hook('testpkg20b/my2.py')
    import testpkg20a.testpkg20b.my2  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(['testpkg20b/my2.py'], self._import_callbacks_log)
    self._import_callbacks_log = []

    # Import with all enclosing packages (the typical case).
    self._Hook('testpkg20b/my3.py')
    import testpkg20a.testpkg20b.my3  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(['testpkg20b/my3.py'], self._import_callbacks_log)
    self._import_callbacks_log = []

  def testFromImportImportsFunction(self):
    self._CreateFile('testpkg21a/__init__.py')
    self._CreateFile('testpkg21a/testpkg21b/__init__.py')
    self._CreateFile('testpkg21a/testpkg21b/mod.py', ('def func1():\n'
                                                      '  return 5\n'
                                                      '\n'
                                                      'def func2():\n'
                                                      '  return 7\n'))

    self._Hook('mod.py')
    from testpkg21a.testpkg21b.mod import func1, func2  # pylint: disable=g-import-not-at-top,unused-variable,g-multiple-import
    self.assertEqual(['mod.py'], self._import_callbacks_log)

  def testImportSibling(self):
    self._CreateFile('testpkg22/__init__.py')
    self._CreateFile('testpkg22/first.py', 'import second')
    self._CreateFile('testpkg22/second.py')

    self._Hook('testpkg22/second.py')
    import testpkg22.first  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(['testpkg22/second.py'], self._import_callbacks_log)

  def testImportSiblingSamePackage(self):
    self._CreateFile('testpkg23/__init__.py')
    self._CreateFile('testpkg23/testpkg23/__init__.py')
    self._CreateFile(
        'testpkg23/first.py',
        'import testpkg23.second')  # This refers to testpkg23.testpkg23.second
    self._CreateFile('testpkg23/testpkg23/second.py')

    self._Hook('testpkg23/testpkg23/second.py')
    import testpkg23.first  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual(['testpkg23/testpkg23/second.py'],
                     self._import_callbacks_log)

  def testImportSiblingFromInit(self):
    self._Hook(self._CreateFile('testpkg23a/__init__.py', 'import testpkg23b'))
    self._Hook(
        self._CreateFile('testpkg23a/testpkg23b/__init__.py',
                         'import testpkg23c'))
    self._Hook(self._CreateFile('testpkg23a/testpkg23b/testpkg23c/__init__.py'))
    import testpkg23a  # pylint: disable=g-import-not-at-top,unused-variable
    self.assertEqual([
        'testpkg23a/__init__.py', 'testpkg23a/testpkg23b/__init__.py',
        'testpkg23a/testpkg23b/testpkg23c/__init__.py'
    ], sorted(self._import_callbacks_log))

  def testThreadLocalCleanup(self):
    self._CreateFile('testpkg24/__init__.py')
    self._CreateFile('testpkg24/foo.py', 'import bar')
    self._CreateFile('testpkg24/bar.py')

    # Create a hook for any arbitrary module. Doesn't need to hit.
    self._Hook('xxx/yyy.py')

    import testpkg24.foo  # pylint: disable=g-import-not-at-top,unused-variable

    self.assertEqual(imphook._import_local.nest_level, 0)
    self.assertEmpty(imphook._import_local.names)

  def testThreadLocalCleanupWithCaughtImportError(self):
    self._CreateFile('testpkg25/__init__.py')
    self._CreateFile(
        'testpkg25/foo.py',
        'import bar\n'  # success.
        'import baz')  # success.
    self._CreateFile('testpkg25/bar.py')
    self._CreateFile(
        'testpkg25/baz.py', 'try:\n'
        '  import testpkg25b\n'
        'except ImportError:\n'
        '  pass')

    # Create a hook for any arbitrary module. Doesn't need to hit.
    self._Hook('xxx/yyy.py')

    # Successful import at top level. Failed import at inner level.
    import testpkg25.foo  # pylint: disable=g-import-not-at-top,unused-variable

    self.assertEqual(imphook._import_local.nest_level, 0)
    self.assertEmpty(imphook._import_local.names)

  def testThreadLocalCleanupWithUncaughtImportError(self):
    self._CreateFile('testpkg26/__init__.py')
    self._CreateFile(
        'testpkg26/foo.py',
        'import bar\n'  # success.
        'import baz')  # fail.
    self._CreateFile('testpkg26/bar.py')

    # Create a hook for any arbitrary module. Doesn't need to hit.
    self._Hook('testpkg26/bar.py')

    # Inner import will fail, and exception will be propagated here.
    try:
      import testpkg26.foo  # pylint: disable=g-import-not-at-top,unused-variable
    except ImportError:
      pass

    # The hook for bar should be invoked, as bar is already loaded.
    self.assertEqual(['testpkg26/bar.py'], self._import_callbacks_log)

    self.assertEqual(imphook._import_local.nest_level, 0)
    self.assertEmpty(imphook._import_local.names)

  def testCleanup(self):
    cleanup1 = self._Hook('a/b/c.py')
    cleanup2 = self._Hook('a/b/c.py')
    cleanup3 = self._Hook('a/d/f.py')
    cleanup4 = self._Hook('a/d/g.py')
    cleanup5 = self._Hook('a/d/c.py')
    self.assertLen(imphook._import_callbacks, 4)

    cleanup1()
    self.assertLen(imphook._import_callbacks, 4)
    cleanup2()
    self.assertLen(imphook._import_callbacks, 3)
    cleanup3()
    self.assertLen(imphook._import_callbacks, 2)
    cleanup4()
    self.assertLen(imphook._import_callbacks, 1)
    cleanup5()
    self.assertLen(imphook._import_callbacks, 0)

  def _CreateFile(self, path, content='', rewrite_imports=True):
    full_path = os.path.join(self._test_package_dir, path)
    directory, unused_name = os.path.split(full_path)

    if not os.path.isdir(directory):
      os.makedirs(directory)

    def RewriteImport(line):
      """Converts import statements to relative form.

      Examples:
        import x => from . import x
        import x.y.z => from .x.y import z
        print('') => print('')

      Args:
        line: str, the line to convert.

      Returns:
        str, the converted import statement or original line.
      """
      original_line_length = len(line)
      line = line.lstrip(' ')
      indent = ' ' * (original_line_length - len(line))
      if line.startswith('import'):
        pkg, _, mod = line.split(' ')[1].rpartition('.')
        line = 'from .%s import %s' % (pkg, mod)
      return indent + line

    with open(full_path, 'w') as writer:
      if rewrite_imports:
        content = '\n'.join(RewriteImport(l) for l in content.split('\n'))
      writer.write(content)

    return path

  # TODO: add test for the module param in the callback.
  def _Hook(self, path, callback=lambda m: None):
    cleanup = imphook.AddImportCallbackBySuffix(
        path, lambda mod:
        (self._import_callbacks_log.append(path), callback(mod)))
    self.assertTrue(cleanup, path)
    self._callback_cleanups.append(cleanup)
    return cleanup


if __name__ == '__main__':
  absltest.main()
