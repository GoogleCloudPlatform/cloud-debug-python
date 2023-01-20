#include "native_test_util.h"

#include "gtest/gtest.h"

#include <fstream>
#include <string>

#include "absl/strings/str_replace.h"
#include "absl/strings/str_split.h"
#include "absl/strings/string_view.h"
#include "include/ghc/filesystem.hpp"
#include "re2/re2.h"

namespace devtools {
namespace cdbg {

TestDebugletModule::TestDebugletModule() {
  module_.reset(PyModule_New(CDBG_MODULE_NAME));
  EXPECT_FALSE(module_.is_null());

  SetDebugletModule(module_.get());
}


TestDebugletModule::~TestDebugletModule() {
  SetDebugletModule(nullptr);
}

std::string GetTestDataFullPath(const std::string& file_name) {
  return (ghc::filesystem::path("googleclouddebugger/testdata/") /
          ghc::filesystem::path(file_name)).string();
}

std::string LoadTestModuleSourceCode(const std::string& file_name) {
  std::string path = GetTestDataFullPath(file_name);

  std::ifstream ifs(path);
  std::string content((std::istreambuf_iterator<char>(ifs)),
                      (std::istreambuf_iterator<char>()));

  // Python expects "\n" and not "\r\n".
  absl::StrReplaceAll({{"\r\n", "\n"}}, &content);

  return content;
}

std::map<std::string, int> MapBreakpointTags(const std::string& source_code) {
  const RE2 regex("# BPTAG: ([0-9a-zA-Z_]+)\\s*$");

  std::vector<std::string> lines = absl::StrSplit(source_code, '\n');

  std::map<std::string, int> tag_map;
  for (uint line_number = 1; line_number <= lines.size(); ++line_number) {
    std::string bp_tag;
    if (!RE2::PartialMatch(lines[line_number - 1], regex, &bp_tag)) {
      continue;  // No breakpoint tag on this line.
    }

    auto it_existing = tag_map.find(bp_tag);
    if (it_existing != tag_map.end()) {
      ADD_FAILURE() << "Same breakpoint tag " << bp_tag << " is used in line "
                    << it_existing->second << " and line " << line_number;
    }

    tag_map[bp_tag] = line_number;
  }

  return tag_map;
}

int MapBreakpointTag(const std::string& source_code,
                     const std::string& tag_name) {
  std::map<std::string, int> tag_map = MapBreakpointTags(source_code);

  auto it = tag_map.find(tag_name);
  if (it == tag_map.end()) {
    ADD_FAILURE() << "Breakpoint tag " << tag_name << " not found";
    return 0;
  }

  return it->second;
}

ScopedPyObject LoadTestModule(const std::string& file_name) {
  std::string source_code = LoadTestModuleSourceCode(file_name);

  ScopedPyObject code_object(
      Py_CompileString(source_code.c_str(), file_name.c_str(), Py_file_input));
  EXPECT_FALSE(code_object.is_null());

  char* name = const_cast<char*>(
      ghc::filesystem::path(file_name).stem().c_str());

  ScopedPyObject module(PyImport_ExecCodeModule(name, code_object.get()));
  EXPECT_FALSE(module.is_null());

  return module;
}

ScopedPyObject GetModuleMethod(PyObject* module, const std::string& name) {
  EXPECT_NE(nullptr, module);
  EXPECT_TRUE(PyModule_CheckExact(module));

  PyObject* module_dict = PyModule_GetDict(module);
  EXPECT_NE(nullptr, module_dict);

  PyObject* function = PyDict_GetItemString(
      module_dict,
      const_cast<char*>(name.c_str()));
  EXPECT_NE(nullptr, function);

  return ScopedPyObject::NewReference(function);
}

PyCodeObject* GetCodeObject(PyObject* method) {
  EXPECT_NE(nullptr, method);
  EXPECT_TRUE(PyFunction_Check(method));

  PyCodeObject* code_object = reinterpret_cast<PyCodeObject*>(
      reinterpret_cast<PyFunctionObject*>(method)->func_code);
  EXPECT_NE(nullptr, code_object);
  EXPECT_TRUE(PyCode_Check(code_object));

  return code_object;
}


void InvokeNoArgs(PyObject* callable) {
  ASSERT_NE(nullptr, callable);
  ASSERT_TRUE(PyCallable_Check(callable));

  ScopedPyObject args(PyTuple_New(0));
  ASSERT_FALSE(args.is_null());

  ScopedPyObject result(PyObject_Call(callable, args.get(), nullptr));
  ASSERT_FALSE(result.is_null());
}

std::string StrPyObject(PyObject* obj) {
  if (obj == nullptr) {
    return "<null>";
  }

  ScopedPyObject obj_str(PyObject_Str(obj));
  EXPECT_FALSE(obj_str.is_null());

  const char* obj_c_str = PyString_AsString(obj_str.get());
  EXPECT_NE(nullptr, obj_c_str);

  return obj_c_str;
}

}  // namespace cdbg
}  // namespace devtools
