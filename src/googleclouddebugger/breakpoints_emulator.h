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

#ifndef DEVTOOLS_CDBG_DEBUGLETS_PYTHON_BREAKPOINTS_EMULATOR_H_
#define DEVTOOLS_CDBG_DEBUGLETS_PYTHON_BREAKPOINTS_EMULATOR_H_

#include <map>
#include <vector>
#include "common.h"
#include "thread_breakpoints.h"
#include "python_util.h"

namespace devtools {
namespace cdbg {

// Sets breakpoints in Python code using the best available mechanism.
//
// Supports multi-threaded environment. This class is thread safe.
class BreakpointsEmulator {
 public:
  BreakpointsEmulator();

  ~BreakpointsEmulator();

  void Initialize(PyObject* self);

  void Detach();

  // Sets a new breakpoint. Returns cookie used to clear the breakpoint.
  int SetBreakpoint(
      PyCodeObject* code_object,
      int source_line,
      BreakpointFn callback);

  void ClearBreakpoint(int cookie);

  // Disables breakpoints emulator for the current thread.
  //
  // This call has no immediate effect if the emulator is already attached
  // to the thread. If function is called when there are no breakpoints set,
  // it is guaranteed to always have the desired effect. This is because
  // breakpoints emulator only attaches to thread when there are active
  // breakpoints.
  static PyObject* DisableDebuggerOnCurrentThread(
      PyObject* self,
      PyObject* py_args);

  // Attaches the debuglet to the current thread.
  //
  // This is only needed for native threads as Python is not even aware they
  // exist. If the debugger is already attached to this thread or if the
  // debugger is disabled for this thread, this function does nothing.
  void AttachNativeThread();

 private:
  // Installs a hook to detect new Python threads.
  void EnableNewThreadsHook(bool enable);

  // Gets list of current Python threads. Assumes single Python interpreter.
  static std::vector<PyThreadState*> GetCurrentThreads();

  // Scans all current threads. New threads (i.e. threads that we haven't
  // seen before) are assigned a their instance of "ThreadBreakpoints" object.
  // Then returns "ThreadBreakpoints" and the corresponding thread state.
  std::map<PyThreadState*, ThreadBreakpoints*> ScanThreads();

  // Called when a new thread is discovered. This function is always called
  // with Interpreter Lock held. It is called after swapping current thread,
  // so it must not try to acquire the Interpreter Lock.
  void AttachCurrentThread();

  // Callback from a newly created thread due to "threading.setprofile".
  static PyObject* ThreadingProfileHook(PyObject* self, PyObject* args);

  // Checks whether the Cloud Debugger is disable on a particular thread.
  static bool IsDebuggerDisabledOnThread(PyObject* thread_dict);

 public:
  // Definition of Python type object.
  static PyTypeObject python_type_;

 private:
  // Weak reference to Python object wrapping this class.
  PyObject* self_;

  // List of active breakpoints;
  std::vector<PythonBreakpoint> breakpoints_;

  // Global counter of breakpoints to generate a unique breakpoint cookie.
  int cookie_counter_;

  // Python method definition wrapping "ThreadingProfileHook".
  PyMethodDef threading_hook_def_;

  // Python method object wrapping "ThreadingProfileHook".
  ScopedPyObject threading_hook_method_;

  // Keeps track of a hook detecting new Python threads.
  bool new_threads_hook_enabled_;

  DISALLOW_COPY_AND_ASSIGN(BreakpointsEmulator);
};

// Python type used as a dictionary key to disable debugger
// on a particular thread.
class DisableDebuggerKey {
 public:
  static PyTypeObject python_type_;
};

}  // namespace cdbg
}  // namespace devtools

#endif  // DEVTOOLS_CDBG_DEBUGLETS_PYTHON_BREAKPOINTS_EMULATOR_H_
