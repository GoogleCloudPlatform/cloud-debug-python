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

// Ensure that Python.h is included before any other header.
#include "common.h"

#include "breakpoints_emulator.h"

#include "python_util.h"

namespace devtools {
namespace cdbg {

PyTypeObject BreakpointsEmulator::python_type_ =
    DefaultTypeDefinition(CDBG_SCOPED_NAME("_BreakpointsEmulator"));

PyTypeObject DisableDebuggerKey::python_type_ =
    DefaultTypeDefinition(CDBG_SCOPED_NAME("_DisableDebuggerKey"));


BreakpointsEmulator::BreakpointsEmulator()
    : self_(nullptr),
      cookie_counter_(1000000),
      new_threads_hook_enabled_(false) {
  memset(&threading_hook_def_, 0, sizeof(threading_hook_def_));

  threading_hook_def_.ml_name = const_cast<char*>("ThreadingProfileHook");
  threading_hook_def_.ml_meth = ThreadingProfileHook;
  threading_hook_def_.ml_flags = METH_VARARGS;
  threading_hook_def_.ml_doc = const_cast<char*>("");
}


BreakpointsEmulator::~BreakpointsEmulator() {
}


void BreakpointsEmulator::Initialize(PyObject* self) {
  self_ = self;
}


void BreakpointsEmulator::Detach() {
  auto* key = reinterpret_cast<PyObject*>(&ThreadBreakpoints::python_type_);

  EnableNewThreadsHook(false);

  std::vector<PyThreadState*> thread_states = GetCurrentThreads();
  for (auto it = thread_states.cbegin(); it != thread_states.cend(); ++it) {
    PyThreadState* thread_state = *it;
    PyObject* thread_dict;
    {
      ScopedThreadStateSwap thread_state_swap(thread_state);
      thread_dict = PyThreadState_GetDict();
    }
    if (thread_dict == nullptr) {
      continue;  // This is not a valid thread.
    }

    PyObject* item = PyDict_GetItem(thread_dict, key);
    if (item == nullptr) {
      continue;  // We never attached to this thread.
    }

    auto* thread_breakpoints = py_object_cast<ThreadBreakpoints>(item);
    if (thread_breakpoints == nullptr) {
      continue;  // We have some bogus object.
    }

    thread_breakpoints->DetachThread();

    if (PyDict_DelItem(thread_dict, key) != 0) {
      LOG(WARNING) << "Failed to detach from the thread";
    }
  }
}


int BreakpointsEmulator::SetBreakpoint(
    PyCodeObject* code_object,
    int source_line,
    BreakpointFn callback) {
  auto threads = ScanThreads();

  PythonBreakpoint new_breakpoint;
  new_breakpoint.cookie = ++cookie_counter_;
  new_breakpoint.code_object = ScopedPyCodeObject::NewReference(code_object);
  new_breakpoint.source_line = source_line;
  new_breakpoint.callback = callback;

  breakpoints_.push_back(new_breakpoint);

  for (auto it = threads.begin(); it != threads.end(); ++it) {
    ScopedThreadStateSwap thread_state_swap(it->first);
    it->second->SetBreakpoint(new_breakpoint);
  }

  EnableNewThreadsHook(true);

  return new_breakpoint.cookie;
}


void BreakpointsEmulator::ClearBreakpoint(int cookie) {
  auto threads = ScanThreads();
  for (auto it = threads.begin(); it != threads.end(); ++it) {
    ScopedThreadStateSwap thread_state_swap(it->first);
    it->second->ClearBreakpoint(cookie);
  }

  // TODO(vlif): clearing all breakpoints incur O(n^2) complexity
  // here. Need a better data structure to support >100 breakpoints.
  for (auto it = breakpoints_.begin(); it != breakpoints_.end(); ) {
    if (it->cookie == cookie) {
      it = breakpoints_.erase(it);
    } else {
      ++it;
    }
  }

  if (breakpoints_.empty()) {
    EnableNewThreadsHook(false);
  }
}


/*static*/ PyObject* BreakpointsEmulator::DisableDebuggerOnCurrentThread(
    PyObject* self,
    PyObject* py_args) {
  PyObject* thread_dict = PyThreadState_GetDict();
  if (thread_dict == nullptr) {
    PyErr_SetString(PyExc_RuntimeError, "thread dictionary not found");
    return nullptr;
  }

  if (PyDict_SetItem(
        thread_dict,
        reinterpret_cast<PyObject*>(&DisableDebuggerKey::python_type_),
        Py_True)) {
    return nullptr;
  }

  Py_INCREF(Py_None);
  return Py_None;
}


void BreakpointsEmulator::AttachNativeThread() {
  PyObject* thread_dict = PyThreadState_GetDict();
  if (thread_dict == nullptr) {
    LOG(ERROR) << "Thread dictionary not found";
    return;
  }

  PyObject* thread_breakpoints = PyDict_GetItem(
      thread_dict,
      reinterpret_cast<PyObject*>(&ThreadBreakpoints::python_type_));
  if ((thread_breakpoints != nullptr) ||
      IsDebuggerDisabledOnThread(thread_dict)) {
    // Debugger already enabled or permanently disabled on this thread.
    return;
  }

  AttachCurrentThread();
}


std::vector<PyThreadState*> BreakpointsEmulator::GetCurrentThreads() {
  std::vector<PyThreadState*> threads;

  PyInterpreterState* interpreter = PyThreadState_Get()->interp;

  PyThreadState* thread = PyInterpreterState_ThreadHead(interpreter);
  while (thread != nullptr) {
    threads.push_back(thread);
    thread = PyThreadState_Next(thread);
  }

  return threads;
}


std::map<PyThreadState*, ThreadBreakpoints*>
BreakpointsEmulator::ScanThreads() {
  auto* key = reinterpret_cast<PyObject*>(&ThreadBreakpoints::python_type_);

  std::vector<PyThreadState*> thread_states = GetCurrentThreads();

  std::map<PyThreadState*, ThreadBreakpoints*> threads;
  for (auto it = thread_states.cbegin(); it != thread_states.cend(); ++it) {
    PyThreadState* thread_state = *it;
    PyObject* thread_dict;
    {
      ScopedThreadStateSwap thread_state_swap(thread_state);

      thread_dict = PyThreadState_GetDict();
    }
    if (thread_dict == nullptr) {
      continue;  // This is not a valid thread.
    }

    PyObject* item = PyDict_GetItem(thread_dict, key);
    if (item == nullptr) {
      if (IsDebuggerDisabledOnThread(thread_dict)) {
        // Debugger disabled for this thread.
        continue;
      }

      ScopedThreadStateSwap thread_state_swap(thread_state);

      AttachCurrentThread();
      item = PyDict_GetItem(thread_dict, key);
    }

    if (item == nullptr) {
      LOG(ERROR) << "Failed to attach to a thread";
      continue;
    }

    auto* thread_breakpoints = py_object_cast<ThreadBreakpoints>(item);
    if (thread_breakpoints == nullptr) {
      LOG(ERROR) << "Bogus per thread breakpoint emulator found";
      continue;  // We have some bogus object.
    }

    threads[thread_state] = thread_breakpoints;
  }

  return threads;
}


void BreakpointsEmulator::AttachCurrentThread() {
  VLOG(1) << "Attaching to a new thread";

  PyObject* thread_dict = PyThreadState_GetDict();
  if (thread_dict == nullptr) {
    return;  // This is not a valid thread.
  }

  ScopedPyObject item = NewNativePythonObject<ThreadBreakpoints>();

  auto* thread_breakpoints = py_object_cast<ThreadBreakpoints>(item.get());
  thread_breakpoints->Initialize(item.get());

  PyDict_SetItem(
      thread_dict,
      reinterpret_cast<PyObject*>(&ThreadBreakpoints::python_type_),
      item.get());

  for (auto it = breakpoints_.cbegin(); it != breakpoints_.cend(); ++it) {
    thread_breakpoints->SetBreakpoint(*it);
  }
}


void BreakpointsEmulator::EnableNewThreadsHook(bool enable) {
  if (new_threads_hook_enabled_ == enable) {
    return;  // Nothing to do.
  }

  ScopedThreadDisableThreadBreakpoints disable_thread_breakpoints;

  ScopedPyObject module(PyImport_ImportModule("threading"));
  if (module == nullptr) {
    LOG(ERROR) << "threading module not found";
    return;
  }

  // Lazily create hook callback.
  if (threading_hook_method_ == nullptr) {
    threading_hook_method_.reset(
        PyCFunction_NewEx(&threading_hook_def_, self_, nullptr));
  }

  ScopedPyObject result(PyObject_CallMethod(
      module.get(),
      const_cast<char*>("setprofile"),
      const_cast<char*>("O"),
      enable ? threading_hook_method_.get() : Py_None));
  if (result == nullptr) {
    LOG(ERROR) << "threading.setprofile failed, enable = " << enable;
  }

  new_threads_hook_enabled_ = enable;
}


PyObject* BreakpointsEmulator::ThreadingProfileHook(
    PyObject* self,
    PyObject* args) {
  // We don't need the thread to be profiled. We only use this hook to detect
  // new threads.
  PyEval_SetProfile(nullptr, nullptr);

  auto* context = py_object_cast<BreakpointsEmulator>(self);
  if (context == nullptr) {
    LOG(ERROR) << "Invalid self";
    Py_RETURN_NONE;
  }

  context->AttachCurrentThread();

  Py_RETURN_NONE;
}


/*static*/ bool BreakpointsEmulator::IsDebuggerDisabledOnThread(
    PyObject* thread_dict) {
  PyObject* debugger_disabled_on_thread = PyDict_GetItem(
      thread_dict,
      reinterpret_cast<PyObject*>(&DisableDebuggerKey::python_type_));
  return (debugger_disabled_on_thread != nullptr) &&
         PyObject_IsTrue(debugger_disabled_on_thread);
}


}  // namespace cdbg
}  // namespace devtools


