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

#ifndef DEVTOOLS_CDBG_DEBUGLETS_PYTHON_THREAD_BREAKPOINTS_H_
#define DEVTOOLS_CDBG_DEBUGLETS_PYTHON_THREAD_BREAKPOINTS_H_

#include <functional>
#include <map>
#include <vector>
#include "common.h"
#include "fast_lru_cache.h"
#include "python_util.h"

namespace devtools {
namespace cdbg {

// Breakpoints emulator will typically notify the next layer when a breakpoint
// hits. However there are other situations that the next layer need to be
// aware of.
enum class BreakpointEvent {
  // The breakpoint was hit.
  Hit,

  // Error occurred (e.g. breakpoint could not be set).
  Error,

  // The breakpoints emulator is consuming too much resources (due to profiler
  // or tracer overhead). It is the responsibility of the next layer to disable
  // all the breakpoints as a response.
  EmulatorQuotaExceeded,

  // Evaluation of conditional expression is consuming too much resources. It is
  // a responsibility of the next layer to disable the offending breakpoint.
  GlobalConditionQuotaExceeded,
  BreakpointConditionQuotaExceeded,

  // The conditional expression changes state of the program and therefore not
  // allowed.
  ConditionExpressionMutable,
};

// Breakpoint callback type. The "frame" field is only relevant if "event"
// is "Hit".
typedef std::function<
    void(BreakpointEvent event, PyFrameObject* frame)> BreakpointFn;

// Internal representation of active breakpoint for breakpoints emulator.
struct PythonBreakpoint {
  // Breakpoint cookie (used to delete the breakpoint).
  int cookie;

  // Code object in which we set the breakpoint.
  ScopedPyCodeObject code_object;

  // Breakpoint line number (1 based).
  int source_line;

  // Callback invoked on breakpoint hit.
  BreakpointFn callback;
};


// Implements Set/ClearBreakpoint functions for a single Python thread.
//
// It is the responsibility of the caller to always call this class from
// the same Python thread.
//
// When changing breakpoints "BreakpointsEmulator" swaps Python thread. As
// a result code in this class gets called from different native threads. It
// should still be safe to change data structures directly without locks. While
// this class manipulates with data structures, it holds Interpreter Lock and
// does not preempt it nor calls anything that may preempt it. In adding a lock
// for internal data structures of this class will cause deadlock with the
// Interpreter Lock.
//
// If resources quota is exceeded, this class does not disable the breakpoint
// (contrary to the comments in "BreakpointEvent"). Instead it lets
// "BreakpointEmulator" to disable the breakpoint on all the threads (including
// this one).
class ThreadBreakpoints {
 public:
  ThreadBreakpoints();

  ~ThreadBreakpoints();

  // "self" is the Python object head. This class should only keep a weak
  // reference to prevent circular reference count.
  // "trace_quota" not owned and must be valid throughout lifetime of this
  // class.
  void Initialize(PyObject* self);

  // Clears all breakpoints and removes the trace function from the thread.
  void DetachThread();

  void SetBreakpoint(const PythonBreakpoint& new_breakpoint);

  void ClearBreakpoint(int cookie);

 private:
  // Enables or disables profiler callback for the current thread.
  void EnableProfileCallback(bool enable);

  // Enables or disables trace callback for the current thread.
  void EnableTraceCallback(bool enable);

  // Python tracer callback function.
  static int OnTraceCallback(
      PyObject* obj,
      PyFrameObject* frame,
      int what,
      PyObject* arg) {
    auto* instance = py_object_cast<ThreadBreakpoints>(obj);
    return instance->OnTraceCallbackInternal(frame, what, arg);
  }

  // Python tracer callback function (instance function for convenience).
  int OnTraceCallbackInternal(PyFrameObject* frame, int what, PyObject* arg);

  // Rebuilds "line_map_" after list of breakpoints has been changed.
  void RebuildLineMap();

  // Updates all the internal data structures upon new or deleted breakpoints.
  void ActiveBreakpointsChanged();

  // Checks whether the code object has a breakpoint set. Employs a fast
  // cache to speed things up.
  bool IsBreakpointAtCodeObject(PyCodeObject* code_object);

 public:
  // Definition of Python type object.
  static PyTypeObject python_type_;

 private:
  // Weak reference to Python object wrapping this class.
  PyObject* self_;

  // List of active breakpoints
  std::vector<PythonBreakpoint> breakpoints_;

  // Maps line numbers to list of breakpoints set on those lines.
  std::map<
      int,
      std::vector<std::vector<PythonBreakpoint>::const_iterator>> line_map_;

  // True if profile callback has already been enabled
  // through "PyEval_SetProfile".
  bool profile_active_;

  // True if line tracer has already been enabled through "PyEval_SetTrace".
  bool trace_active_;

  // Flag indicating that the thread is inside a breakpoint callback. Trace
  // callbacks are disabled at that time.
  bool in_callback_;

  // Small LRU cache to speed up "IsBreakpointAtCodeObject".
  FastLRUCache<ScopedPyCodeObject, bool> is_breakpoint_at_code_object_cache_;

  DISALLOW_COPY_AND_ASSIGN(ThreadBreakpoints);
};


// Class to temporarily disable the functionality of "ThreadBreakpoints" in
// the current native thread.
class ScopedThreadDisableThreadBreakpoints {
 public:
  ScopedThreadDisableThreadBreakpoints();

  ~ScopedThreadDisableThreadBreakpoints();

  DISALLOW_COPY_AND_ASSIGN(ScopedThreadDisableThreadBreakpoints);
};

}  // namespace cdbg
}  // namespace devtools

#endif  // DEVTOOLS_CDBG_DEBUGLETS_PYTHON_THREAD_BREAKPOINTS_H_
