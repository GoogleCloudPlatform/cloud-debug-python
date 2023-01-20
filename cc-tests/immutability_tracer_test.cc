#include "src/googleclouddebugger/immutability_tracer.h"

#include <functional>

#include <benchmark/benchmark.h>
#include <gtest/gtest.h>

#include "native_test_util.h"

namespace devtools {
namespace cdbg {

static constexpr char kMutableCodeError[] = "<class 'SystemError'>";

class ImmutabilityTracerTest : public testing::Test {
 protected:
  void SetUp() override {
    EXPECT_TRUE(RegisterPythonType<ImmutabilityTracer>());
  }

  void TearDown() override {
    ClearPythonException();
  }

  // Common code for python modules that should evaluate successfully.
  void TestPositive(const std::string& source_code,
                    const std::string& expected_result) {
    ScopedPyObject result = TestCommon(source_code);

    auto exception = ClearPythonException();
    ASSERT_FALSE(exception.has_value())
        << "Source code:" << std::endl << source_code << std::endl
        << "Exception: " << exception.value();

    ASSERT_EQ(expected_result, StrPyObject(result.get())) << source_code;
  }

  // Common code for python modules that should fail to evaluate.
  void TestNegative(const std::string& source_code,
                    const std::string& expected_error) {
    ScopedPyObject result = TestCommon(source_code);
    ASSERT_TRUE(result.is_null()) << source_code;

    PyObject* exception_obj = PyErr_Occurred();
    ASSERT_NE(nullptr, exception_obj);
    EXPECT_EQ(expected_error, StrPyObject(exception_obj));
  }

  ScopedPyObject TestCommon(const std::string& source_code) {
    ScopedPyObject code_object(Py_CompileString(
        source_code.c_str(),
        "<string>",
        Py_file_input));
    EXPECT_FALSE(code_object.is_null());

    ScopedPyObject module(PyImport_ExecCodeModule(
        const_cast<char*>("module_from_string"),
        code_object.get()));
    EXPECT_FALSE(module.is_null());

    PyObject* module_dict = PyModule_GetDict(module.get());
    EXPECT_NE(nullptr, module_dict);

    PyObject* function = PyDict_GetItemString(
        module_dict,
        const_cast<char*>("test"));
    EXPECT_NE(nullptr, function);

    ScopedPyObject args(PyTuple_New(0));
    EXPECT_FALSE(args.is_null());

    ScopedImmutabilityTracer immutability_tracer;
    return ScopedPyObject(PyObject_Call(function, args.get(), nullptr));
  }

 protected:
  TestDebugletModule debuglet_module_;
};


TEST_F(ImmutabilityTracerTest, Basic) {
  constexpr char kSourceCode[] =
      "a = 37\n"
      "b = 'hello'\n"
      "def test():\n"
      "  return 'a = ' + str(a) + ', b = ' + b";
  TestPositive(kSourceCode, "a = 37, b = hello");
}


TEST_F(ImmutabilityTracerTest, InstanceFields) {
  constexpr char kSourceCode[] =
      "class TestClass(object):\n"
      "  def __init__(self):\n"
      "    self.x = 'important'\n"
      "  def work(self):\n"
      "    return self.x + ' work'\n"
      "t = TestClass()\n"
      "def test():\n"
      "  return t.work()";
  TestPositive(kSourceCode, "important work");
}


TEST_F(ImmutabilityTracerTest, ClassToString) {
  constexpr char kSourceCode[] =
      "class TestClass(object):\n"
      "  def __str__(self):\n"
      "    return 'Proud to be TestClass'\n"
      "t = TestClass()\n"
      "def test():\n"
      "  return str(t)";
  TestPositive(kSourceCode, "Proud to be TestClass");
}


TEST_F(ImmutabilityTracerTest, ChangeIfBlock) {
  constexpr char kSourceCode[] =
      "x = 8\n"
      "def test():\n"
      "  if x % 2 == 0:\n"
      "    return 'x = ' + str(x) + ' (even)'\n"
      "  else:\n"
      "    return 'x = ' + str(x) + ' (odd)'";
  TestPositive(kSourceCode, "x = 8 (even)");
}


TEST_F(ImmutabilityTracerTest, ChangeLocals) {
  constexpr char kSourceCode[] =
      "x = 8\n"
      "def test():\n"
      "  x = 9\n"
      "  return 'x = ' + str(x)";
  TestPositive(kSourceCode, "x = 9");
}


TEST_F(ImmutabilityTracerTest, ChangeGlobal) {
  constexpr char kSourceCode[] =
      "x = 8\n"
      "def test():\n"
      "  global x\n"
      "  x = 9";
  TestNegative(kSourceCode, kMutableCodeError);
}


TEST_F(ImmutabilityTracerTest, SystemFunction) {
  constexpr char kSourceCode[] =
      "def test():\n"
      "  open('/tmp/myfile')";
  TestNegative(kSourceCode, kMutableCodeError);
}


TEST_F(ImmutabilityTracerTest, ExceptionPropagation) {
  constexpr char kSourceCode[] =
      "zero = 0\n"
      "def test():\n"
      "  return 1 / zero";
  TestNegative(kSourceCode, "<class 'ZeroDivisionError'>");
}


TEST_F(ImmutabilityTracerTest, MultilineWhileLoop) {
  constexpr char kSourceCode[] =
      "def test():\n"
      "  i = 2\n"
      "  while i < 100:\n"
      "    i = i * 2\n"
      "  return i";
  TestPositive(kSourceCode, "128");
}


TEST_F(ImmutabilityTracerTest, InfiniteWhileLoop) {
  constexpr char kSourceCode[] =
      "def test():\n"
      "  while True: pass";
  TestNegative(kSourceCode, kMutableCodeError);
}


TEST_F(ImmutabilityTracerTest, InfiniteForLoop) {
  constexpr char kSourceCode[] =
      "r = range(1000000000)\n"
      "def test():\n"
      "  for _ in r: pass";
  TestNegative(kSourceCode, kMutableCodeError);
}


TEST_F(ImmutabilityTracerTest, MutatingBuiltins) {
  constexpr char kSourceCode[] =
      "class X(object):\n"
      "  pass\n"
      "x = X()\n"
      "def test():\n"
      "  x.__setattr__('a', 1)";
  TestNegative(kSourceCode, kMutableCodeError);
}


}  // namespace cdbg
}  // namespace devtools
