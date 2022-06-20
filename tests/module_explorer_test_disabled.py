"""Unit test for module_explorer module."""

# TODO: Get this test to run properly on all supported versions of Python

import dis
import inspect
import os
import py_compile
import shutil
import sys
import tempfile

from absl.testing import absltest

from googleclouddebugger import module_explorer
import python_test_util


class ModuleExplorerTest(absltest.TestCase):
  """Unit test for module_explorer module."""

  def setUp(self):
    self._module = sys.modules[__name__]
    self._code_objects = module_explorer._GetModuleCodeObjects(self._module)

    # Populate line cache for this module (neeed .par test).
    inspect.getsourcelines(self.testCodeObjectAtLine)

  def testGlobalMethod(self):
    """Verify that global method is found."""
    self.assertIn(_GlobalMethod.__code__, self._code_objects)

  def testInnerMethodOfGlobalMethod(self):
    """Verify that inner method defined in a global method is found."""
    self.assertIn(_GlobalMethod(), self._code_objects)

  def testInstanceClassMethod(self):
    """Verify that instance class method is found."""
    self.assertIn(self.testInstanceClassMethod.__code__, self._code_objects)

  def testInnerMethodOfInstanceClassMethod(self):
    """Verify that inner method defined in a class instance method is found."""

    def InnerMethod():
      pass

    self.assertIn(InnerMethod.__code__, self._code_objects)

  def testStaticMethod(self):
    """Verify that static class method is found."""
    self.assertIn(ModuleExplorerTest._StaticMethod.__code__, self._code_objects)

  def testInnerMethodOfStaticMethod(self):
    """Verify that static class method is found."""
    self.assertIn(ModuleExplorerTest._StaticMethod(), self._code_objects)

  def testNonModuleClassMethod(self):
    """Verify that instance method defined in a base class is not added."""
    self.assertNotIn(self.assertTrue.__code__, self._code_objects)

  def testDeepInnerMethod(self):
    """Verify that inner of inner of inner, etc. method is found."""

    def Inner1():

      def Inner2():

        def Inner3():

          def Inner4():

            def Inner5():
              pass

            return Inner5.__code__

          return Inner4()

        return Inner3()

      return Inner2()

    self.assertIn(Inner1(), self._code_objects)

  def testNoLambdaExpression(self):
    """Verify that code of lambda expression is not included."""

    self.assertNotIn(_MethodWithLambdaExpression(), self._code_objects)

  def testNoGeneratorExpression(self):
    """Verify that code of generator expression is not included."""

    self.assertNotIn(_MethodWithGeneratorExpression(), self._code_objects)

  def testMethodOfInnerClass(self):
    """Verify that method of inner class is found."""

    class InnerClass(object):

      def InnerClassMethod(self):
        pass

    self.assertIn(InnerClass().InnerClassMethod.__code__, self._code_objects)

  def testMethodOfInnerOldStyleClass(self):
    """Verify that method of inner old style class is found."""

    class InnerClass():

      def InnerClassMethod(self):
        pass

    self.assertIn(InnerClass().InnerClassMethod.__code__, self._code_objects)

  def testGlobalMethodWithClosureDecorator(self):
    co = self._GetCodeObjectAtLine(self._module,
                                   'GLOBAL_METHOD_WITH_CLOSURE_DECORATOR')
    self.assertTrue(co)
    self.assertEqual('GlobalMethodWithClosureDecorator', co.co_name)

  def testClassMethodWithClosureDecorator(self):
    co = self._GetCodeObjectAtLine(
        self._module, 'GLOBAL_CLASS_METHOD_WITH_CLOSURE_DECORATOR')
    self.assertTrue(co)
    self.assertEqual('FnWithClosureDecorator', co.co_name)

  def testGlobalMethodWithClassDecorator(self):
    co = self._GetCodeObjectAtLine(self._module,
                                   'GLOBAL_METHOD_WITH_CLASS_DECORATOR')
    self.assertTrue(co)
    self.assertEqual('GlobalMethodWithClassDecorator', co.co_name)

  def testClassMethodWithClassDecorator(self):
    co = self._GetCodeObjectAtLine(self._module,
                                   'GLOBAL_CLASS_METHOD_WITH_CLASS_DECORATOR')
    self.assertTrue(co)
    self.assertEqual('FnWithClassDecorator', co.co_name)

  def testSameFileName(self):
    """Verify that all found code objects are defined in the same file."""
    path = next(iter(self._code_objects)).co_filename
    self.assertTrue(path)

    for code_object in self._code_objects:
      self.assertEqual(path, code_object.co_filename)

  def testCodeObjectAtLine(self):
    """Verify that query of code object at a specified source line."""
    test_cases = [
        (self.testCodeObjectAtLine.__code__, 'TEST_CODE_OBJECT_AT_ASSERT'),
        (ModuleExplorerTest._StaticMethod(), 'INNER_OF_STATIC_METHOD'),
        (_GlobalMethod(), 'INNER_OF_GLOBAL_METHOD')
    ]

    for code_object, tag in test_cases:
      self.assertEqual(  # BPTAG: TEST_CODE_OBJECT_AT_ASSERT
          code_object, self._GetCodeObjectAtLine(code_object, tag))

  def testCodeObjectWithoutModule(self):
    """Verify no crash/hang when module has no file name."""
    global global_code_object  # pylint: disable=global-variable-undefined
    global_code_object = compile('2+3', '<string>', 'exec')

    self.assertFalse(
        module_explorer.GetCodeObjectAtLine(self._module, 111111)[0])


# TODO: Re-enable this test, without hardcoding a python version into it.
#  def testCodeExtensionMismatch(self):
#    """Verify module match when code object points to .py and module to .pyc."""
#    test_dir = tempfile.mkdtemp('', 'module_explorer_')
#    sys.path.append(test_dir)
#    try:
#      # Create and compile module, remove the .py file and leave the .pyc file.
#      module_path = os.path.join(test_dir, 'module.py')
#      with open(module_path, 'w') as f:
#        f.write('def f():\n  pass')
#      py_compile.compile(module_path)
#      module_pyc_path = os.path.join(test_dir, '__pycache__',
#                                     'module.cpython-37.pyc')
#      os.rename(module_pyc_path, module_path + 'c')
#      os.remove(module_path)
#
#      import module  # pylint: disable=g-import-not-at-top
#      self.assertEqual('.py',
#                       os.path.splitext(module.f.__code__.co_filename)[1])
#      self.assertEqual('.pyc', os.path.splitext(module.__file__)[1])
#
#      func_code = module.f.__code__
#      self.assertEqual(func_code,
#                       module_explorer.GetCodeObjectAtLine(
#                           module,
#                           next(dis.findlinestarts(func_code))[1])[1])
#    finally:
#      sys.path.remove(test_dir)
#      shutil.rmtree(test_dir)

  def testMaxVisitObjects(self):
    default_quota = module_explorer._MAX_VISIT_OBJECTS
    try:
      module_explorer._MAX_VISIT_OBJECTS = 10
      self.assertLess(
          len(module_explorer._GetModuleCodeObjects(self._module)),
          len(self._code_objects))
    finally:
      module_explorer._MAX_VISIT_OBJECTS = default_quota

  def testMaxReferentsBfsDepth(self):
    default_quota = module_explorer._MAX_REFERENTS_BFS_DEPTH
    try:
      module_explorer._MAX_REFERENTS_BFS_DEPTH = 1
      self.assertLess(
          len(module_explorer._GetModuleCodeObjects(self._module)),
          len(self._code_objects))
    finally:
      module_explorer._MAX_REFERENTS_BFS_DEPTH = default_quota

  def testMaxObjectReferents(self):

    class A(object):
      pass

    default_quota = module_explorer._MAX_VISIT_OBJECTS
    default_referents_quota = module_explorer._MAX_OBJECT_REFERENTS
    try:
      global large_dict
      large_dict = {A(): 0 for i in range(0, 5000)}

      # First test with a referents limit too large, it will visit large_dict
      # and exhaust the _MAX_VISIT_OBJECTS quota before finding all the code
      # objects
      module_explorer._MAX_VISIT_OBJECTS = 5000
      module_explorer._MAX_OBJECT_REFERENTS = sys.maxsize
      self.assertLess(
          len(module_explorer._GetModuleCodeObjects(self._module)),
          len(self._code_objects))

      # Now test with a referents limit that prevents large_dict from being
      # explored, all the code objects should be found now that the large dict
      # is skipped and isn't taking up the _MAX_VISIT_OBJECTS quota
      module_explorer._MAX_OBJECT_REFERENTS = default_referents_quota
      self.assertItemsEqual(
          module_explorer._GetModuleCodeObjects(self._module),
          self._code_objects)
    finally:
      module_explorer._MAX_VISIT_OBJECTS = default_quota
      module_explorer._MAX_OBJECT_REFERENTS = default_referents_quota
      large_dict = None

  @staticmethod
  def _StaticMethod():

    def InnerMethod():
      pass  # BPTAG: INNER_OF_STATIC_METHOD

    return InnerMethod.__code__

  def _GetCodeObjectAtLine(self, fn, tag):
    """Wrapper over GetCodeObjectAtLine for tags in this module."""
    unused_path, line = python_test_util.ResolveTag(fn, tag)
    return module_explorer.GetCodeObjectAtLine(self._module, line)[1]


def _GlobalMethod():

  def InnerMethod():
    pass  # BPTAG: INNER_OF_GLOBAL_METHOD

  return InnerMethod.__code__


def ClosureDecorator(handler):

  def Caller(*args):
    return handler(*args)

  return Caller


class ClassDecorator(object):

  def __init__(self, fn):
    self._fn = fn

  def __call__(self, *args):
    return self._fn(*args)


@ClosureDecorator
def GlobalMethodWithClosureDecorator():
  return True  # BPTAG: GLOBAL_METHOD_WITH_CLOSURE_DECORATOR


@ClassDecorator
def GlobalMethodWithClassDecorator():
  return True  # BPTAG: GLOBAL_METHOD_WITH_CLASS_DECORATOR


class GlobalClass(object):

  @ClosureDecorator
  def FnWithClosureDecorator(self):
    return True  # BPTAG: GLOBAL_CLASS_METHOD_WITH_CLOSURE_DECORATOR

  @ClassDecorator
  def FnWithClassDecorator(self):
    return True  # BPTAG: GLOBAL_CLASS_METHOD_WITH_CLASS_DECORATOR


def _MethodWithLambdaExpression():
  return (lambda x: x**3).__code__


def _MethodWithGeneratorExpression():
  return (i for i in range(0, 2)).gi_code


# Used for testMaxObjectReferents, need to be in global scope or else the module
# explorer would not explore this
large_dict = None

if __name__ == '__main__':
  absltest.main()
