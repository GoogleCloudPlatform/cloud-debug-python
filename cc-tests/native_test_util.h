#ifndef DEVTOOLS_CDBG_DEBUGLETS_PYTHON_NATIVE_TEST_UTIL_H_
#define DEVTOOLS_CDBG_DEBUGLETS_PYTHON_NATIVE_TEST_UTIL_H_

#include <map>
#include <memory>

#include "src/googleclouddebugger/common.h"
#include "src/googleclouddebugger/python_util.h"

namespace devtools {
namespace cdbg {

// Fake module to act as a debuglet module.
class TestDebugletModule {
 public:
  TestDebugletModule();

  ~TestDebugletModule();

 private:
  ScopedPyObject module_;

  DISALLOW_COPY_AND_ASSIGN(TestDebugletModule);
};

// Gets path to testdata file.
std::string GetTestDataFullPath(const std::string& file_name);

// Loads ".py" file in "testdata" directory. Fails the test if not found.
std::string LoadTestModuleSourceCode(const std::string& file_name);

// Returns map of breakpoint tags to line number in the specified Python
// source file. Breakpoint tags are appended to the source code as comments
// as following
//     print "regular code"  # BPTAG: TAGNAME
std::map<std::string, int> MapBreakpointTags(const std::string& source_code);

// Searches for breakpoint tag in the source code. If found returns line
// number. Otherwise fails the test and returns 0.
int MapBreakpointTag(const std::string& source_code,
                     const std::string& tag_name);

// Loads the specified Python module from "testdata" directory.
ScopedPyObject LoadTestModule(const std::string& file_name);

// Gets a global method from the module.
ScopedPyObject GetModuleMethod(PyObject* module, const std::string& name);

// Gets the code object of a Python method.
PyCodeObject* GetCodeObject(PyObject* method);

// Executes a Python callable with no arguments.
void InvokeNoArgs(PyObject* callable);

// Equivalent to "str(o)" in Python.
std::string StrPyObject(PyObject* obj);

}  // namespace cdbg
}  // namespace devtools

#endif  // DEVTOOLS_CDBG_DEBUGLETS_PYTHON_NATIVE_TEST_UTIL_H_
