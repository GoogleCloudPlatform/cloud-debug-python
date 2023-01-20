#include "src/googleclouddebugger/python_callback.h"

#include "native_test_util.h"
#include "src/googleclouddebugger/python_util.h"
#include "gtest/gtest.h"

namespace devtools {
namespace cdbg {

class PythonCallbackTest : public testing::Test {
 protected:
  void SetUp() override {
    EXPECT_TRUE(RegisterPythonType<PythonCallback>());
  }

  void TearDown() override {
  }

 protected:
  TestDebugletModule debuglet_module_;
};


TEST_F(PythonCallbackTest, Wrap) {
  ScopedPyObject callback = PythonCallback::Wrap([] () { });
  ASSERT_FALSE(callback.is_null());
}


TEST_F(PythonCallbackTest, Invoke) {
  int counter = 0;

  ScopedPyObject callback1 =
      PythonCallback::Wrap([&counter] () { counter += 1; });
  ScopedPyObject callback2 =
      PythonCallback::Wrap([&counter] () { counter += 100; });

  ScopedPyObject args(PyTuple_New(0));
  ASSERT_FALSE(args.is_null());

  ScopedPyObject result;

  result.reset(PyObject_Call(callback1.get(), args.get(), nullptr));
  ASSERT_EQ(Py_None, result.get());
  ASSERT_EQ(1, counter);

  result.reset(PyObject_Call(callback1.get(), args.get(), nullptr));
  ASSERT_EQ(Py_None, result.get());
  ASSERT_EQ(2, counter);

  result.reset(PyObject_Call(callback2.get(), args.get(), nullptr));
  ASSERT_EQ(Py_None, result.get());
  ASSERT_EQ(102, counter);

  result.reset(PyObject_Call(callback2.get(), args.get(), nullptr));
  ASSERT_EQ(Py_None, result.get());
  ASSERT_EQ(202, counter);

  result.reset(PyObject_Call(callback2.get(), args.get(), nullptr));
  ASSERT_EQ(Py_None, result.get());
  ASSERT_EQ(302, counter);

  result.reset(PyObject_Call(callback1.get(), args.get(), nullptr));
  ASSERT_EQ(Py_None, result.get());
  ASSERT_EQ(303, counter);
}


TEST_F(PythonCallbackTest, Disable) {
  int counter = 0;

  ScopedPyObject callback =
      PythonCallback::Wrap([&counter] () { counter += 1; });

  ScopedPyObject args(PyTuple_New(0));
  ASSERT_FALSE(args.is_null());

  ScopedPyObject result;

  result.reset(PyObject_Call(callback.get(), args.get(), nullptr));
  ASSERT_EQ(Py_None, result.get());
  ASSERT_EQ(1, counter);

  PythonCallback::Disable(callback.get());

  result.reset(PyObject_Call(callback.get(), args.get(), nullptr));
  ASSERT_EQ(Py_None, result.get());
  ASSERT_EQ(1, counter);
}

}  // namespace cdbg
}  // namespace devtools

