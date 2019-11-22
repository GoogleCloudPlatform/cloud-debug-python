/**
 * Copyright 2015 Google Inc. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef DEVTOOLS_CDBG_DEBUGLETS_PYTHON_BYTECODE_BREAKPOINT_H_
#define DEVTOOLS_CDBG_DEBUGLETS_PYTHON_BYTECODE_BREAKPOINT_H_

#include <map>
#include <unordered_map>
#include <vector>

#include "common.h"
#include "python_util.h"

namespace devtools {
namespace cdbg {

// Sets breakpoints in Python code with zero runtime overhead.
// BytecodeBreakpoint rewrites Python bytecode to insert a breakpoint. The
// implementation is specific to CPython 2.7.
// TODO: rename to BreakpointsEmulator when the original implementation
// of BreakpointsEmulator goes away.
class BytecodeBreakpoint {
 public:
  BytecodeBreakpoint();

  ~BytecodeBreakpoint();

  // Clears all the set breakpoints.
  void Detach();

  // Sets a new breakpoint in the specified code object. More than one
  // breakpoint can be set at the same source location. When the breakpoint
  // hits, the "callback" parameter is invoked. Every time this class fails to
  // install the breakpoint, "error_callback" is invoked. Returns cookie used
  // to clear the breakpoint.
  int SetBreakpoint(
      PyCodeObject* code_object,
      int line,
      std::function<void()> hit_callback,
      std::function<void()> error_callback);

  // Removes a previously set breakpoint. If the cookie is invalid, this
  // function does nothing.
  void ClearBreakpoint(int cookie);

 private:
  // Information about the breakpoint.
  struct Breakpoint {
    // Method in which the breakpoint is set.
    ScopedPyCodeObject code_object;

    // Line number on which the breakpoint is set.
    int line;

    // Offset to the instruction on which the breakpoint is set.
    int offset;

    // Python callable object to invoke on breakpoint hit.
    ScopedPyObject hit_callable;

    // Callback to invoke every time this class fails to install
    // the breakpoint.
    std::function<void()> error_callback;

    // Breakpoint ID used to clear the breakpoint.
    int cookie;
  };

  // Set of breakpoints in a particular code object and original data of
  // the code object to clear breakpoints.
  struct CodeObjectBreakpoints {
    // Patched code object.
    ScopedPyCodeObject code_object;

    // Maps breakpoint offset to breakpoint information. The map is sorted in
    // a descending order.
    std::multimap<int, Breakpoint*, std::greater<int>> breakpoints;

    // Python runtime assumes that objects referenced by "PyCodeObject" stay
    // alive as long as the code object is alive. Therefore when patching the
    // code object, we can't just decrement reference count for code and
    // constants. Instead we store these references in a special zombie pool.
    // Then once we know that no Python thread is executing the code object,
    // we can release all of them.
    // TODO: implement garbage collection for zombie refs.
    std::vector<ScopedPyObject> zombie_refs;

    // Original value of PyCodeObject::co_stacksize before patching.
    int original_stacksize;

    // Original value of PyCodeObject::co_consts before patching.
    ScopedPyObject original_consts;

    // Original value of PyCodeObject::co_code before patching.
    ScopedPyObject original_code;

    // Original value of PythonCode::co_lnotab before patching.
    // "lnotab" stands for "line numbers table" in CPython lingo.
    ScopedPyObject original_lnotab;
  };

  // Loads code object into "patches_" if not there yet. Returns nullptr if
  // the code object has no code or corrupted.
  CodeObjectBreakpoints* PreparePatchCodeObject(
      const ScopedPyCodeObject& code_object);

  // Patches the code object with breakpoints. If the code object has no more
  // breakpoints, resets the code object to its original state. This operation
  // is idempotent.
  void PatchCodeObject(CodeObjectBreakpoints* code);

 private:
  // Global counter of breakpoints to generate a unique breakpoint cookie.
  int cookie_counter_;

  // Maps breakpoint cookie to full breakpoint information.
  std::map<int, Breakpoint*> cookie_map_;

  // Patched code objects.
  std::unordered_map<
      ScopedPyCodeObject,
      CodeObjectBreakpoints*,
      ScopedPyCodeObject::Hash> patches_;

  DISALLOW_COPY_AND_ASSIGN(BytecodeBreakpoint);
};

}  // namespace cdbg
}  // namespace devtools

#endif  // DEVTOOLS_CDBG_DEBUGLETS_PYTHON_BYTECODE_BREAKPOINT_H_
