#include "src/googleclouddebugger/bytecode_breakpoint.h"

#include <functional>

#include <string>

#include <benchmark/benchmark.h>
#include <gtest/gtest.h>

#include "native_test_util.h"

#include "src/googleclouddebugger/common.h"
#include "src/googleclouddebugger/python_callback.h"
#include "src/googleclouddebugger/python_util.h"

#include "absl/strings/str_join.h"

#define EXPECT_NO_EXCEPTION() ExpectNoException(__FILE__, __LINE__)

namespace devtools {
namespace cdbg {

// If the source code size (in characters) exceeds this threshold, the
// test will not print it and will not try to disassemble it.
static constexpr int kSourceCodeSizeThreshold = 100000;

// no-op function to be used for callbacks.
static void NoopCallback()  { }

class BytecodeBreakpointTest : public testing::Test {
 protected:
  struct TestMethod {
    std::string source_code;
    ScopedPyObject method;
  };

  void SetUp() override {
    EXPECT_TRUE(RegisterPythonType<PythonCallback>());

    ScopedPyObject dis_module(PyImport_ImportModule("dis"));
    EXPECT_NO_EXCEPTION();
    EXPECT_FALSE(dis_module.is_null());

    PyObject* dis_module_dict = PyModule_GetDict(dis_module.get());
    EXPECT_NO_EXCEPTION();
    ASSERT_NE(nullptr, dis_module_dict);

    dis_ = ScopedPyObject::NewReference(
        PyDict_GetItemString(dis_module_dict, "dis"));
    EXPECT_NO_EXCEPTION();
    ASSERT_FALSE(dis_.is_null());
  }

  void TearDown() override {
    emulator_.Detach();
  }

  static void UnexpectedBreakpointFailure() {
    ADD_FAILURE() << "Failed to install the breakpoint";
  }

  static void ExpectNoException(const char* file, int line) {
    if (PyErr_Occurred() != nullptr) {
      PyObject *ptype, *pvalue, *ptraceback;
      PyErr_Fetch(&ptype, &pvalue, &ptraceback);
      if (pvalue) {
        PyObject *pstr = PyObject_Str(pvalue);
        if (pstr) {
          ADD_FAILURE_AT(file, line) << "Python Code Exception: "
                                     << PyUnicode_AsUTF8(pstr);
        }
      }
      PyErr_Restore(ptype, pvalue, ptraceback);
      PyErr_Clear();
    }
  }

  static PyCodeObject* GetCodeObject(const TestMethod& test_method) {
    EXPECT_FALSE(test_method.method.is_null());

    if (PyFunction_Check(test_method.method.get())) {
      PyCodeObject* code_object = reinterpret_cast<PyCodeObject*>(
          PyFunction_GetCode(test_method.method.get()));
      EXPECT_NE(nullptr, code_object);
      EXPECT_TRUE(PyCode_Check(code_object));
      return code_object;
    }

    if (PyCode_Check(test_method.method.get())) {
      return reinterpret_cast<PyCodeObject*>(test_method.method.get());
    }

    ADD_FAILURE() << "Invalid type of test method";
    return nullptr;
  }

  void Disassemble(PyObject* obj) {
    LOG(INFO) << "Disassembling method:";

    ScopedPyObject args(PyTuple_New(1));
    EXPECT_NO_EXCEPTION();
    ASSERT_FALSE(args.is_null());

    Py_XINCREF(obj);
    PyTuple_SET_ITEM(args.get(), 0, obj);
    EXPECT_NO_EXCEPTION();

    ScopedPyObject result(PyObject_Call(dis_.get(), args.get(), nullptr));
    EXPECT_NO_EXCEPTION();
    ASSERT_FALSE(result.is_null());
  }

  TestMethod DefineMethod(const std::vector<std::string>& lines) {
    static int module_counter = 1;

    std::string module_name = "dynamic_module" + std::to_string(module_counter);
    std::string file_name = module_name + ".py";
    ++module_counter;

    std::string source_code = absl::StrJoin(lines, "\n");
    bool is_huge = (source_code.size() >= kSourceCodeSizeThreshold);

    LOG(INFO) << "Loading Python code:"
              << std::endl
              << (is_huge ? "<redacted>" : source_code);

    ScopedPyObject code_object(Py_CompileString(
        source_code.c_str(),
        file_name.c_str(),
        Py_file_input));
    EXPECT_NO_EXCEPTION();
    EXPECT_FALSE(code_object.is_null());

    ScopedPyObject module(PyImport_ExecCodeModule(
        const_cast<char*>(module_name.c_str()),
        code_object.get()));
    EXPECT_NO_EXCEPTION();
    EXPECT_FALSE(module.is_null());

    PyObject* module_dict = PyModule_GetDict(module.get());
    EXPECT_NO_EXCEPTION();
    EXPECT_NE(nullptr, module_dict);

    PyObject* method = PyDict_GetItemString(module_dict, "test");
    EXPECT_NO_EXCEPTION();
    EXPECT_NE(nullptr, method);
    EXPECT_TRUE(PyCallable_Check(method));

    if (!is_huge) {
      Disassemble(method);
    }

    return { source_code, ScopedPyObject::NewReference(method) };
  }

  TestMethod GetInnerMethod(const TestMethod& test_method,
                            const std::string& name) {
    PyCodeObject* outer = GetCodeObject(test_method);
    for (int i = 0; i < PyTuple_GET_SIZE(outer->co_consts); ++i) {
      PyObject* item = PyTuple_GET_ITEM(outer->co_consts, i);
      if (!PyCode_Check(item)) {
        continue;
      }

      PyCodeObject* inner = reinterpret_cast<PyCodeObject*>(item);
      if (name == PyString_AsString(inner->co_name)) {
        return { test_method.source_code, ScopedPyObject::NewReference(item) };
      }
    }

    ADD_FAILURE() << "Inner method " << name << " not found";
    return {std::string(), ScopedPyObject()};
  }

  int CreateBreakpoint(const TestMethod& test_method, const std::string& tag,
                       std::function<void()> hit_callback, std::function<void()>
                       error_callback = UnexpectedBreakpointFailure) {
    int line = MapBreakpointTag(test_method.source_code, tag);
    LOG(INFO) << "Creating new breakpoint at line: " << line;

    const int cookie = emulator_.CreateBreakpoint(
        GetCodeObject(test_method),
        line,
        hit_callback,
        UnexpectedBreakpointFailure);
    EXPECT_GT(cookie, 0);

    EXPECT_EQ(BreakpointStatus::kInactive,
            emulator_.GetBreakpointStatus(cookie));

    LOG(INFO) << "Created breakpoint with cookie: " << cookie;

    return cookie;
  }

  void ActivateBreakpoint(int cookie) {
    LOG(INFO) << "Activating breakpoint with cookie: " << cookie;
    emulator_.ActivateBreakpoint(cookie);
    EXPECT_EQ(BreakpointStatus::kActive,
              emulator_.GetBreakpointStatus(cookie));
  }

  void ClearBreakpoint(int cookie) {
    LOG(INFO) << "Clearing breakpoint with cookie: " << cookie;
    emulator_.ClearBreakpoint(cookie);
    EXPECT_EQ(BreakpointStatus::kUnknown,
              emulator_.GetBreakpointStatus(cookie));
  }

  int SetBreakpoint(const TestMethod& test_method, const std::string& tag,
                    std::function<void()> hit_callback, std::function<void()>
                    error_callback = UnexpectedBreakpointFailure) {
    const int cookie = CreateBreakpoint(test_method, tag, hit_callback,
                                        error_callback);

    ActivateBreakpoint(cookie);

    EXPECT_NO_EXCEPTION();

    if (test_method.source_code.size() < kSourceCodeSizeThreshold) {
      Disassemble(test_method.method.get());
    }

    return cookie;
  }

  int CreateCountingBreakpoint(const TestMethod& test_method,
                               const std::string& tag, int* counter) {
    return CreateBreakpoint(
        test_method,
        tag,
        [counter] () {
          LOG(INFO) << "Breakpoint hit";
          *counter += 1;
        });
  }

  int SetCountingBreakpoint(const TestMethod& test_method,
                            const std::string& tag, int* counter) {
    return SetBreakpoint(
        test_method,
        tag,
        [counter] () {
          LOG(INFO) << "Breakpoint hit";
          *counter += 1;
        });
  }

  ScopedPyObject CallMethod(PyObject* method) {
    ScopedPyObject args(PyTuple_New(0));
    EXPECT_NO_EXCEPTION();
    EXPECT_FALSE(args.is_null());

    ScopedPyObject result(PyObject_Call(method, args.get(), nullptr));
    EXPECT_NO_EXCEPTION();
    EXPECT_FALSE(result.is_null());

    return result;
  }

 protected:
  TestDebugletModule debuglet_module_;
  ScopedPyObject dis_;
  BytecodeBreakpoint emulator_;
};


TEST_F(BytecodeBreakpointTest, TrivialInsert) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  return 'hello' # BPTAG: HELLO",
  });

  SetCountingBreakpoint(test_method, "HELLO", nullptr);
}


TEST_F(BytecodeBreakpointTest, TrivialAppend) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  yield 'hello' # BPTAG: HELLO",
  });

  SetCountingBreakpoint(test_method, "HELLO", nullptr);
}


TEST_F(BytecodeBreakpointTest, Simple) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  print('Before')",
    "  print('After')  # BPTAG: MIDDLE"
  });

  int counter = 0;
  SetCountingBreakpoint(test_method, "MIDDLE", &counter);

  for (int i = 0; i < 5; ++i) {
    CallMethod(test_method.method.get());
  }

  EXPECT_EQ(5, counter);
}


TEST_F(BytecodeBreakpointTest, SetBreakpointNullCodeObject) {
  bool failed = false;
  emulator_.CreateBreakpoint(
      nullptr,
      0,
      [] () {},
      [&failed] () { failed = true; });

  EXPECT_TRUE(failed);
}


TEST_F(BytecodeBreakpointTest, SetBreakpointNotCodeObject) {
  ScopedPyObject module(PyImport_ImportModule("threading"));
  EXPECT_NO_EXCEPTION();
  EXPECT_FALSE(module.is_null());

  bool failed = false;
  emulator_.CreateBreakpoint(
      reinterpret_cast<PyCodeObject*>(module.get()),
      0,
      NoopCallback,
      [&failed] () { failed = true; });

  EXPECT_TRUE(failed);
}


TEST_F(BytecodeBreakpointTest, ExistingConsts) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  x = 123456789",
    "  x = x + 1",
    "  return x  # BPTAG: RETURNING"
  });

  int counter = 0;
  SetCountingBreakpoint(test_method, "RETURNING", &counter);

  ScopedPyObject rc = CallMethod(test_method.method.get());
  ASSERT_TRUE(PyInt_CheckExact(rc.get()));
  ASSERT_EQ(123456789 + 1, PyInt_AsLong(rc.get()));

  EXPECT_EQ(1, counter);
}


TEST_F(BytecodeBreakpointTest, OutOfRangeLineNumber) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  pass"
  });

  bool failed;

  failed = false;
  int cookie = emulator_.CreateBreakpoint(
      GetCodeObject(test_method),
      -1,
      NoopCallback,
      [&failed] () { failed = true; });
  emulator_.ActivateBreakpoint(cookie);

  EXPECT_TRUE(failed);

  failed = false;
  cookie = emulator_.CreateBreakpoint(
      GetCodeObject(test_method),
      3,
      NoopCallback,
      [&failed] () { failed = true; });
  emulator_.ActivateBreakpoint(cookie);

  EXPECT_TRUE(failed);
}


TEST_F(BytecodeBreakpointTest, ForLoop) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  for i in range(5):",
    "    print(i)  # BPTAG: INSIDE_LOOP"
  });

  int counter = 0;
  SetCountingBreakpoint(test_method, "INSIDE_LOOP", &counter);

  CallMethod(test_method.method.get());

  EXPECT_EQ(5, counter);
}


TEST_F(BytecodeBreakpointTest, ElseNotHit) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  if 2 > 1:",
    "    print('2 > 1')",
    "  else:",
    "    print('2 <= 1')  # BPTAG: ELSE_NOT_HIT",
    "  return 8"
  });

  int counter = 0;
  SetCountingBreakpoint(test_method, "ELSE_NOT_HIT", &counter);

  CallMethod(test_method.method.get());

  EXPECT_EQ(0, counter);
}


TEST_F(BytecodeBreakpointTest, IfSkipHit) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  if 1 > 2:",
    "    return",
    "  print('1 <= 2')  # BPTAG: IF_SKIP_HIT"
  });

  int counter = 0;
  SetCountingBreakpoint(test_method, "IF_SKIP_HIT", &counter);

  CallMethod(test_method.method.get());

  EXPECT_EQ(1, counter);
}


TEST_F(BytecodeBreakpointTest, Except) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  try:",
    "    raise RuntimeException()",
    "  except:  # BPTAG: EXCEPT",
    "    print('Exception handler')"
  });

  int counter = 0;
  SetCountingBreakpoint(test_method, "EXCEPT", &counter);

  CallMethod(test_method.method.get());

  EXPECT_EQ(1, counter);
}


TEST_F(BytecodeBreakpointTest, With) {
  TestMethod test_method = DefineMethod({
    "import threading",
    "",
    "def test():",
    "  with threading.Lock() as my_lock: # BPTAG: LOCKING",
    "    print('In lock scope')  # BPTAG: IN",
    "  print('Out of lock scope')  # BPTAG: OUT"
  });

  int counter_locking = 0;
  SetCountingBreakpoint(test_method, "LOCKING", &counter_locking);

  int counter_in = 0;
  SetCountingBreakpoint(test_method, "IN", &counter_in);

  int counter_out = 0;
  SetCountingBreakpoint(test_method, "OUT", &counter_out);

  CallMethod(test_method.method.get());

  EXPECT_EQ(1, counter_locking);
  EXPECT_EQ(1, counter_in);
  EXPECT_EQ(1, counter_out);
}


TEST_F(BytecodeBreakpointTest, HugeCode) {
  // Each "n = n + 1" line takes 10 bytes, so 20K such lines gives up
  // far more than the minimum 65K to trigger EXTENDED_ARG.
  constexpr int kExtendedCount = 20000;

  std::vector<std::string> lines;

  lines.push_back("def test():");
  lines.push_back("  n = 1");
  for (int i = 0; i < kExtendedCount; ++i) {
    lines.push_back("  n = n + 1");
  }
  lines.push_back("  for i in range(5):");
  for (int i = 0; i < kExtendedCount; ++i) {
    lines.push_back("    n = n + 1");
  }
  lines.push_back("    if i % 2:");
  for (int i = 0; i < kExtendedCount; ++i) {
    lines.push_back("      n = n + 1");
  }
  lines.push_back("      print('Odd: %d' % i)  # BPTAG: ODD");
  for (int i = 0; i < kExtendedCount; ++i) {
    lines.push_back("      n = n + 1");
  }
  lines.push_back("    else:");
  for (int i = 0; i < kExtendedCount; ++i) {
    lines.push_back("      n = n + 1");
  }
  lines.push_back("      print('Even: %d' % i)  # BPTAG: EVEN");
  for (int i = 0; i < kExtendedCount; ++i) {
    lines.push_back("      n = n + 1");
  }

  TestMethod test_method = DefineMethod(lines);
  EXPECT_GT(PyBytes_Size(GetCodeObject(test_method)->co_code), 0x10000);

  int counter_odd = 0;
  SetCountingBreakpoint(test_method, "ODD", &counter_odd);

  int counter_even = 0;
  SetCountingBreakpoint(test_method, "EVEN", &counter_even);

  CallMethod(test_method.method.get());

  EXPECT_EQ(2, counter_odd);  // 1 and 3
  EXPECT_EQ(3, counter_even);  // 0, 2 and 4
}


TEST_F(BytecodeBreakpointTest, MultipleBreakpoinsSameFunction) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  print('Hello1')  # BPTAG: MULTIPLE_PRINT_1",
    "  print('Hello2')  # BPTAG: MULTIPLE_PRINT_2",
    "  print('Hello3')  # BPTAG: MULTIPLE_PRINT_3",
    "  print('Hello4')  # BPTAG: MULTIPLE_PRINT_4",
    "  print('Hello5')  # BPTAG: MULTIPLE_PRINT_5"
  });

  int hit_counter[] = { 0, 0, 0, 0, 0 };
  SetCountingBreakpoint(test_method, "MULTIPLE_PRINT_1", &hit_counter[0]);
  SetCountingBreakpoint(test_method, "MULTIPLE_PRINT_4", &hit_counter[3]);
  SetCountingBreakpoint(test_method, "MULTIPLE_PRINT_3", &hit_counter[2]);
  SetCountingBreakpoint(test_method, "MULTIPLE_PRINT_5", &hit_counter[4]);
  SetCountingBreakpoint(test_method, "MULTIPLE_PRINT_2", &hit_counter[1]);

  for (int i = 0; i < 3; ++i) {
    CallMethod(test_method.method.get());
  }

  for (int i = 0; i < 5; ++i) {
    EXPECT_EQ(3, hit_counter[i]) << "i = " << i;
  }
}


TEST_F(BytecodeBreakpointTest, MultipleBreakpointsSameLine) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  print('Hello there')  # BPTAG: SIMPLE_PRINT"
  });

  int hit_counter[] = { 0, 0, 0 };
  SetCountingBreakpoint(test_method, "SIMPLE_PRINT", &hit_counter[0]);
  SetCountingBreakpoint(test_method, "SIMPLE_PRINT", &hit_counter[1]);
  SetCountingBreakpoint(test_method, "SIMPLE_PRINT", &hit_counter[2]);

  CallMethod(test_method.method.get());

  EXPECT_EQ(1, hit_counter[0]);
  EXPECT_EQ(1, hit_counter[1]);
  EXPECT_EQ(1, hit_counter[2]);
}

TEST_F(BytecodeBreakpointTest, ActivatingMultipleBreakpointsAtOnce) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  print('Hello there')  # BPTAG: SIMPLE_PRINT"
  });

  int cookies[] = { -1, -1 };
  int hit_counter[] = { 0, 0 };
  cookies[0] = CreateCountingBreakpoint(test_method, "SIMPLE_PRINT",
                                        &hit_counter[0]);
  cookies[1] = CreateCountingBreakpoint(test_method, "SIMPLE_PRINT",
                                        &hit_counter[1]);

  // Ensure that none of them are activated yet.
  CallMethod(test_method.method.get());
  EXPECT_EQ(0, hit_counter[0]);
  EXPECT_EQ(0, hit_counter[1]);

  // Activate breakpoints.
  ActivateBreakpoint(cookies[0]);
  ActivateBreakpoint(cookies[1]);

  // All hit counters should now be 1.
  CallMethod(test_method.method.get());
  EXPECT_EQ(1, hit_counter[0]);
  EXPECT_EQ(1, hit_counter[1]);

  // Clear breakpoints.
  ClearBreakpoint(cookies[0]);
  ClearBreakpoint(cookies[1]);
}

TEST_F(BytecodeBreakpointTest, ActivateMultipleBreakpointsIncrementally) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  print('Hello there')  # BPTAG: SIMPLE_PRINT"
  });

  int cookies[] = { -1, -1 };
  int hit_counter[] = { 0, 0 };
  cookies[0] = CreateCountingBreakpoint(test_method, "SIMPLE_PRINT",
                                        &hit_counter[0]);
  cookies[1] = CreateCountingBreakpoint(test_method, "SIMPLE_PRINT",
                                        &hit_counter[1]);

  // Activate first breakpoint
  ActivateBreakpoint(cookies[0]);

  // Only first breakpoint should get hit.
  CallMethod(test_method.method.get());
  EXPECT_EQ(1, hit_counter[0]);
  EXPECT_EQ(0, hit_counter[1]);

  // Activate second breakpoint.
  ActivateBreakpoint(cookies[1]);

  // Both of them should get hit now.
  CallMethod(test_method.method.get());
  EXPECT_EQ(2, hit_counter[0]);
  EXPECT_EQ(1, hit_counter[1]);

  // Clear first breakpoint.
  ClearBreakpoint(cookies[0]);

  // Only second one should get hit now.
  CallMethod(test_method.method.get());
  EXPECT_EQ(2, hit_counter[0]);
  EXPECT_EQ(2, hit_counter[1]);

  // Cleanup second breakpoint.
  ClearBreakpoint(cookies[1]);
}

TEST_F(BytecodeBreakpointTest, ActivatingMultipleBreakpointsHybrid) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  print('Hello there')  # BPTAG: SIMPLE_PRINT"
  });

  int cookies[] = { -1, -1, -1 };
  int hit_counter[] = { 0, 0, 0 };
  cookies[0] = CreateCountingBreakpoint(test_method, "SIMPLE_PRINT",
                                        &hit_counter[0]);
  cookies[1] = CreateCountingBreakpoint(test_method, "SIMPLE_PRINT",
                                        &hit_counter[1]);
  cookies[2] = CreateCountingBreakpoint(test_method, "SIMPLE_PRINT",
                                        &hit_counter[2]);

  // Activate first two breakpoints.
  ActivateBreakpoint(cookies[0]);
  ActivateBreakpoint(cookies[1]);

  // First two hit counters should now be 1.
  CallMethod(test_method.method.get());
  EXPECT_EQ(1, hit_counter[0]);
  EXPECT_EQ(1, hit_counter[1]);
  EXPECT_EQ(0, hit_counter[2]);

  // Activate third breakpoint.
  ActivateBreakpoint(cookies[2]);

  // All counters should now be incremented.
  CallMethod(test_method.method.get());
  EXPECT_EQ(2, hit_counter[0]);
  EXPECT_EQ(2, hit_counter[1]);
  EXPECT_EQ(1, hit_counter[2]);

  // Clear breakpoints.
  ClearBreakpoint(cookies[0]);
  ClearBreakpoint(cookies[1]);
  ClearBreakpoint(cookies[2]);
}

TEST_F(BytecodeBreakpointTest, GetBreakpointUnknown) {
  EXPECT_EQ(BreakpointStatus::kUnknown, emulator_.GetBreakpointStatus(-1));
}

TEST_F(BytecodeBreakpointTest, GetBreakpointInactive) {
  TestMethod test_method = DefineMethod({
    "def test(): ",
    "  pass  # BPTAG: TEST"
  });

  int cookie = CreateBreakpoint(test_method, "TEST", nullptr);
  EXPECT_NE(-1, cookie);
  EXPECT_EQ(BreakpointStatus::kInactive, emulator_.GetBreakpointStatus(cookie));

  ClearBreakpoint(cookie);
}

TEST_F(BytecodeBreakpointTest, GetBreakpointActive) {
  TestMethod test_method = DefineMethod({
    "def test(): ",
    "  print('Hello')  # BPTAG: TEST"
  });

  int hit_counter = 0;
  int cookie = SetCountingBreakpoint(test_method, "TEST", &hit_counter);
  EXPECT_NE(-1, cookie);
  emulator_.ActivateBreakpoint(cookie);
  EXPECT_EQ(BreakpointStatus::kActive, emulator_.GetBreakpointStatus(cookie));

  CallMethod(test_method.method.get());
  EXPECT_EQ(1, hit_counter);

  ClearBreakpoint(cookie);

  // Ensure that breakpoint was actually cleared.
  CallMethod(test_method.method.get());
  EXPECT_EQ(1, hit_counter);
}

// It is hard to find simple examples where the debugger fails to set the
// breakpoint, after it was created (which is good). It would only fail if
// BreakpointManipulator's methods do. Since it will require a big change to the
// interface to be able to mock BreakpointManipulator and inject it into
// our BytecodeBreakpoint class, we instead utilize a scenario which doesn't
// happen often but we are confident would cause a failure.
TEST_F(BytecodeBreakpointTest, GetBreakpointError) {
  // In this test, we force the debugger to fail by setting the breakpoint on a
  // yield opcode and thus the manipulator would use the append strategy.
  // As an example of what will happen, the first breakpoint will do this:
  //  =======================================================================
  //  Original bytecode:
  //      0 LOAD_CONST          1 ('hello1')
  //      2 YIELD_VALUE
  //      4 POP_TOP
  //      6 LOAD_CONST          0 (None)
  //      8 RETURN_VALUE
  //  -----------------------------------------------------------------------
  //  After first breakpoint:
  //      0 JUMP_ABSOLUTE       10
  //  >>  2 YIELD_VALUE
  //      4 POP_TOP
  //      6 LOAD_CONST          0 (None)
  //      8 RETURN_VALUE
  //  >> 10 LOAD_CONST          2 (<cdbg_native._Callback object>)
  //     12 CALL_FUNCTION       0
  //     14 POP_TOP
  //     16 LOAD_CONST          1 ('hello1')
  //    *18 JUMP_ABSOLUTE       2
  //  =======================================================================
  //  The bytecode at line 18 indicated by a * is what we will use to cause the
  //  error, if we keep adding more breakpoints the size of the code will
  //  increase and cause the instruction size of the jump at line 0 to be of
  //  size 4 instead of 2. Thus, YIELD_VALUE on line 2 will be relocated and
  //  thus the jump at line 18 will cause an error indicating that we are
  //  trying to jump to an instruction that relocated. Check
  //  BytecodeManipulator::AppendMethodCall for more details.
  TestMethod test_method = DefineMethod({
    "def test(): ",
    "  yield 'hello1' # BPTAG: TEST1",
  });

  bool failed = false;

  std::vector<int> cookies_;
  // Lambda function for setting the breakpoint.
  auto SetBreakpoint_ = [&test_method, &failed, &cookies_, this]()
  {
    int line = MapBreakpointTag(test_method.source_code, "TEST1");
    PyCodeObject* code_object = GetCodeObject(test_method);

    // Lambda function for the callback handler.
    int cookie = -1;
    auto ErrorCallback_ = [&cookie, &failed, this]() {
      failed = true;
      // Ensure that the cookie is known and it is an error.
      EXPECT_NE(-1, cookie);
      EXPECT_EQ(BreakpointStatus::kError,
                emulator_.GetBreakpointStatus(cookie));
    };

    cookie = emulator_.CreateBreakpoint(
        code_object,
        line,
        [] () { },
        ErrorCallback_);
    EXPECT_NE(-1, cookie);
    emulator_.ActivateBreakpoint(cookie);
    cookies_.push_back(cookie);
  };

  // From the above discussion, this while loop is guaranteed to exit after
  // ~25 breakpoints, as soon as the bytecode size becomes at least 0xFF.
  PyCodeObject* code_object = GetCodeObject(test_method);
  while (Py_SIZE(code_object->co_code) < 0xFF) {
    SetBreakpoint_();
    // Ensure that it succeeds.
    EXPECT_EQ(BreakpointStatus::kActive,
              emulator_.GetBreakpointStatus(cookies_.back()));
  }

  // The breakpoint shouldn't have failed at this point.
  EXPECT_FALSE(failed);

  // This one should fail, as it will push the code size over 0xFF.
  SetBreakpoint_();
  EXPECT_TRUE(failed);

  // Get value of failing cookie.
  int failing_cookie = cookies_.back();
  cookies_.pop_back();
  EXPECT_EQ(BreakpointStatus::kError,
            emulator_.GetBreakpointStatus(failing_cookie));

  // Get any successful cookie.
  int success_cookie = cookies_.back();
  cookies_.pop_back();
  EXPECT_EQ(BreakpointStatus::kActive,
            emulator_.GetBreakpointStatus(success_cookie));

  // Clearing any successful cookie will call PatchCode and thus automatically
  // retry and successfully activate the failing cookie.
  ClearBreakpoint(success_cookie);
  EXPECT_EQ(BreakpointStatus::kActive,
            emulator_.GetBreakpointStatus(failing_cookie));

  // Clear all other breakpoints.
  ClearBreakpoint(failing_cookie);
  for (auto cookie : cookies_) ClearBreakpoint(cookie);
}

TEST_F(BytecodeBreakpointTest, ClearBreakpoint) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  print('Hello1')  # BPTAG: PRINT_1",
    "  print('Hello2')  # BPTAG: PRINT_2",
    "  print('Hello3')  # BPTAG: PRINT_3"
  });

  int hit_counter1 = 0;
  int cookie1 = SetCountingBreakpoint(test_method, "PRINT_1", &hit_counter1);

  int hit_counter2 = 0;
  int cookie2 = SetCountingBreakpoint(test_method, "PRINT_2", &hit_counter2);

  int hit_counter3 = 0;
  int cookie3 = SetCountingBreakpoint(test_method, "PRINT_2", &hit_counter3);

  CallMethod(test_method.method.get());
  EXPECT_EQ(1, hit_counter1);
  EXPECT_EQ(1, hit_counter2);
  EXPECT_EQ(1, hit_counter3);

  emulator_.ClearBreakpoint(cookie2);
  CallMethod(test_method.method.get());
  EXPECT_EQ(2, hit_counter1);
  EXPECT_EQ(1, hit_counter2);
  EXPECT_EQ(2, hit_counter3);

  emulator_.ClearBreakpoint(cookie1);
  CallMethod(test_method.method.get());
  EXPECT_EQ(2, hit_counter1);
  EXPECT_EQ(1, hit_counter2);
  EXPECT_EQ(3, hit_counter3);

  emulator_.ClearBreakpoint(cookie3);
  CallMethod(test_method.method.get());
  EXPECT_EQ(2, hit_counter1);
  EXPECT_EQ(1, hit_counter2);
  EXPECT_EQ(3, hit_counter3);

  emulator_.ClearBreakpoint(cookie2);
  CallMethod(test_method.method.get());
  EXPECT_EQ(2, hit_counter1);
  EXPECT_EQ(1, hit_counter2);
  EXPECT_EQ(3, hit_counter3);
}


TEST_F(BytecodeBreakpointTest, ClearOnHitSimple) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  print('Hello there')  # BPTAG: SIMPLE_PRINT"
  });

  int counter = 0;
  int cookie = -1;
  cookie = SetBreakpoint(
      test_method,
      "SIMPLE_PRINT",
      [this, &counter, &cookie] () {
        ++counter;
        emulator_.ClearBreakpoint(cookie);
      });

  CallMethod(test_method.method.get());
  EXPECT_EQ(1, counter);

  CallMethod(test_method.method.get());
  EXPECT_EQ(1, counter);
}


TEST_F(BytecodeBreakpointTest, ClearOnHitWith) {
  TestMethod test_method = DefineMethod({
    "class MockResource(object):",
    "  def __init__(self):",
    "    print('MockResource: init')",
    "",
    "  def __enter__(self):",
    "    print('MockResource: enter')",
    "    return self",
    "",
    "  def __exit__(self, type, value, traceback):",
    "    print('MockResource: exit')",
    "",
    "def test():",
    "  with MockResource() as m:",
    "    print('Resource %s' % m)  # BPTAG: IN_WITH"
  });

  int counter = 0;
  int cookie = SetBreakpoint(
      test_method,
      "IN_WITH",
      [this, &counter, &cookie] () {
        counter += 1;
        emulator_.ClearBreakpoint(cookie);
      });

  CallMethod(test_method.method.get());
  EXPECT_EQ(1, counter);
}


TEST_F(BytecodeBreakpointTest, SetOnHitYield) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  def gen():",
    "    yield 'a' # BPTAG: YIELD1",
    "    yield 'b' # BPTAG: YIELD2",
    "    yield 'c' # BPTAG: YIELD3",
    "  it = gen().__iter__()",
    "  try:",
    "    print(next(it))",
    "    print('Now setting breakpoint in existing generator') # BPTAG: START",
    "    while True:",
    "      print(next(it))",
    "  except StopIteration:",
    "    pass"
  });

  TestMethod gen_method = GetInnerMethod(test_method, "gen");

  int counter = 0;
  SetBreakpoint(
      test_method,
      "START",
      [this, &counter, &gen_method] () {
        SetCountingBreakpoint(gen_method, "YIELD1", &counter);
        SetCountingBreakpoint(gen_method, "YIELD2", &counter);
        SetCountingBreakpoint(gen_method, "YIELD3", &counter);
      });

  CallMethod(test_method.method.get());
  EXPECT_EQ(2, counter);
}


TEST_F(BytecodeBreakpointTest, ClearOnHitYield) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  def gen():",
    "    yield 'a'",
    "    yield 'b' # BPTAG: YIELD",
    "    yield 'c'",
    "  print(list(gen()))",
  });

  int counter = 0;
  int cookie = SetBreakpoint(
      GetInnerMethod(test_method, "gen"),
      "YIELD",
      [this, &counter, &cookie] () {
        counter += 1;
        emulator_.ClearBreakpoint(cookie);
      });

  CallMethod(test_method.method.get());
  EXPECT_EQ(1, counter);
}


TEST_F(BytecodeBreakpointTest, ClearOnExceptionsYield) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  def gen():",
    "    source = ['first', 'second', 'third']",
    "    i = 0",
    "    try:",
    "      while True:",
    "        print('About to yield for %d' % i)",
    "        yield source[i]",
    "        i += 1  # BPTAG: INCREMENT",
    "    except IndexError:",
    "      return",
    "  print(list(gen()))"
  });

  int counter = 0;
  int cookie = SetBreakpoint(
      GetInnerMethod(test_method, "gen"),
      "INCREMENT",
      [this, &counter, &cookie] () {
        counter += 1;
        emulator_.ClearBreakpoint(cookie);
      });

  CallMethod(test_method.method.get());
  EXPECT_EQ(1, counter);
}


TEST_F(BytecodeBreakpointTest, MultipleBreakpointsSameLocationYield) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  def gen():",
    "    for i in range(4):",
    "      yield i  # BPTAG: YIELD",
    "  print(list(gen()))"
  });

  TestMethod gen_method = GetInnerMethod(test_method, "gen");

  int counter = 0;
  SetCountingBreakpoint(gen_method, "YIELD", &counter);
  SetCountingBreakpoint(gen_method, "YIELD", &counter);
  SetCountingBreakpoint(gen_method, "YIELD", &counter);

  CallMethod(test_method.method.get());
  EXPECT_EQ(4 * 3, counter);
}


TEST_F(BytecodeBreakpointTest, ClearOneOfMethodBreakpoints) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  print('Hello1')  # BPTAG: PRINT_1",
    "  print('Hello2')  # BPTAG: PRINT_2"
  });

  int hit_counter1 = 0;
  int cookie1 = -1;
  cookie1 = SetBreakpoint(
      test_method,
      "PRINT_2",
      [this, &hit_counter1, &cookie1] () {
        ++hit_counter1;
        emulator_.ClearBreakpoint(cookie1);
      });

  int hit_counter2 = 0;
  SetCountingBreakpoint(test_method, "PRINT_1", &hit_counter2);

  CallMethod(test_method.method.get());
  EXPECT_EQ(1, hit_counter1);
  EXPECT_EQ(1, hit_counter2);

  CallMethod(test_method.method.get());
  EXPECT_EQ(1, hit_counter1);
  EXPECT_EQ(2, hit_counter2);
}

TEST_F(BytecodeBreakpointTest, TestUpdateOffset) {
  std::vector<std::string> lines;
  lines.push_back("def test():");
  // Buffer instruction to make the bytecode the right size needed for added
  // EXTENDED_ARGS to make previously calculated offsets invalid.
  lines.push_back("  n = 1");
  lines.push_back("  for _ in range(1):");
  lines.push_back("    for _ in range(1):");
  lines.push_back("      for _ in range(2):");
  lines.push_back("        range(1)  # BPTAG: 1");
  lines.push_back("        range(1)  # BPTAG: 2");
  lines.push_back("        range(1)  # BPTAG: 3");
  for (int i = 0; i < 26; i++) {
    lines.push_back("        range(1)");
  }

  int counter = 0;
  TestMethod test_method = DefineMethod(lines);
  SetCountingBreakpoint(test_method, "1", &counter);
  SetCountingBreakpoint(test_method, "2", &counter);
  SetCountingBreakpoint(test_method, "3", &counter);

  // Without updating the offset, BPTAG: 1 gets pushed right before the FOR_ITER
  // condition check of the last for loop, which gets executed 3 times instead
  // of the 2 times the range(1) gets executed.
  CallMethod(test_method.method.get());
  EXPECT_EQ(6, counter);
}

TEST_F(BytecodeBreakpointTest, YieldFrom) {
  TestMethod test_method = DefineMethod({
    "def test():",
    "  def gen():",
    "    yield from range(1) # BPTAG: YIELD1",
    "    yield from range(1) # BPTAG: YIELD2",
    "    yield from range(1) # BPTAG: YIELD3",
    "  it = gen().__iter__()",
    "  try:",
    "    print(next(it))",
    "    print('Now setting breakpoint in existing generator') # BPTAG: START",
    "    while True:",
    "      print(next(it))",
    "  except StopIteration:",
    "    pass"
  });

  TestMethod gen_method = GetInnerMethod(test_method, "gen");

  int counter1 = 0;
  int counter2 = 0;
  SetBreakpoint(
      test_method,
      "START",
      [this, &counter1, &counter2, &gen_method] () {
        SetCountingBreakpoint(gen_method, "YIELD1", &counter1);
        SetCountingBreakpoint(gen_method, "YIELD2", &counter2);
        SetCountingBreakpoint(gen_method, "YIELD3", &counter2);
      });

  CallMethod(test_method.method.get());
  EXPECT_EQ(0, counter1);
  EXPECT_EQ(2, counter2);
}


}  // namespace cdbg
}  // namespace devtools
