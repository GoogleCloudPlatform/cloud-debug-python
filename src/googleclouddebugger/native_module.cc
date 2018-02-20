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

#include "bytecode_breakpoint.h"
#include "common.h"
#include "conditional_breakpoint.h"
#include "immutability_tracer.h"
#include "native_module.h"
#include "python_callback.h"
#include "python_util.h"
#include "rate_limit.h"

using google::LogMessage;

namespace devtools {
namespace cdbg {

const LogSeverity LOG_SEVERITY_INFO = ::google::INFO;
const LogSeverity LOG_SEVERITY_WARNING = ::google::WARNING;
const LogSeverity LOG_SEVERITY_ERROR = ::google::ERROR;

struct INTEGER_CONSTANT {
  const char* name;
  int32 value;
};

static const INTEGER_CONSTANT kIntegerConstants[] = {
  {
    "BREAKPOINT_EVENT_HIT",
    static_cast<int32>(BreakpointEvent::Hit)
  },
  {
    "BREAKPOINT_EVENT_ERROR",
    static_cast<int32>(BreakpointEvent::Error)
  },
  {
    "BREAKPOINT_EVENT_GLOBAL_CONDITION_QUOTA_EXCEEDED",
    static_cast<int32>(BreakpointEvent::GlobalConditionQuotaExceeded)
  },
  {
    "BREAKPOINT_EVENT_BREAKPOINT_CONDITION_QUOTA_EXCEEDED",
    static_cast<int32>(BreakpointEvent::BreakpointConditionQuotaExceeded)
  },
  {
    "BREAKPOINT_EVENT_CONDITION_EXPRESSION_MUTABLE",
    static_cast<int32>(BreakpointEvent::ConditionExpressionMutable)
  }
};

// Class to set zero overhead breakpoints.
static BytecodeBreakpoint g_bytecode_breakpoint;

// Initializes C++ flags and logging.
//
// This function should be called exactly once during debugger bootstrap. It
// should be called before any other method in this module is used.
//
// If omitted, the module will stay with default C++ flag values and logging
// will go to stderr.
//
// Args:
//   flags: dictionary of all the flags (flags that don't match names of C++
//          flags will be ignored).
static PyObject* InitializeModule(PyObject* self, PyObject* py_args) {
  PyObject* flags = nullptr;
  if (!PyArg_ParseTuple(py_args, "O", &flags)) {
    return nullptr;
  }

  // Default to log to stderr unless explicitly overridden through flags.
  FLAGS_logtostderr = true;

  if (flags != Py_None) {
    if (!PyDict_Check(flags)) {
      PyErr_SetString(PyExc_TypeError, "flags must be None or a dictionary");
      return nullptr;
    }

    ScopedPyObject flag_items(PyDict_Items(flags));
    if (flag_items == nullptr) {
      PyErr_SetString(PyExc_TypeError, "Failed to iterate over items of flags");
      return nullptr;
    }

    int64 count = PyList_Size(flag_items.get());
    for (int64 i = 0; i < count; ++i) {
      PyObject* tuple = PyList_GetItem(flag_items.get(), i);
      if (tuple == nullptr) {  // Bad index (PyList_GetItem sets an exception).
        return nullptr;
      }

      const char* flag_name = nullptr;
      PyObject* flag_value_obj = nullptr;
      if (!PyArg_ParseTuple(tuple, "sO", &flag_name, &flag_value_obj)) {
        return nullptr;
      }

      ScopedPyObject flag_value_str_obj(PyObject_Str(flag_value_obj));
      if (flag_value_str_obj == nullptr) {
        PyErr_SetString(PyExc_TypeError, "Flag conversion to a string failed");
        return nullptr;
      }

      const char* flag_value = PyString_AsString(flag_value_str_obj.get());
      if (flag_value == nullptr) {  // Exception was already raised.
        return nullptr;
      }

      google::SetCommandLineOption(flag_name, flag_value);
    }
  }

  google::InitGoogleLogging("googleclouddebugger");

  Py_RETURN_NONE;
}


// Common code for LogXXX functions.
//
// The source file name and the source line are obtained automatically by
// inspecting the call stack.
//
// Args:
//   message: message to log.
//
// Returns: None
static PyObject* LogCommon(LogSeverity severity, PyObject* py_args) {
  const char* message = nullptr;
  if (!PyArg_ParseTuple(py_args, "s", &message)) {
    return nullptr;
  }

  const char* file_name = "<unknown>";
  int line = -1;

  PyFrameObject* frame = PyThreadState_Get()->frame;
  if (frame != nullptr) {
    file_name = PyString_AsString(frame->f_code->co_filename);
    line = PyFrame_GetLineNumber(frame);
  }

  // We only log file name, not the full path.
  if (file_name != nullptr) {
    const char* directory_end = strrchr(file_name, '/');
    if (directory_end != nullptr) {
      file_name = directory_end + 1;
    }
  }

  LogMessage(file_name, line, severity).stream() << message;

  Py_RETURN_NONE;
}


// Logs a message at INFO level from Python code.
static PyObject* LogInfo(PyObject* self, PyObject* py_args) {
  return LogCommon(LOG_SEVERITY_INFO, py_args);
}

// Logs a message at WARNING level from Python code.
static PyObject* LogWarning(PyObject* self, PyObject* py_args) {
  return LogCommon(LOG_SEVERITY_WARNING, py_args);
}


// Logs a message at ERROR level from Python code.
static PyObject* LogError(PyObject* self, PyObject* py_args) {
  return LogCommon(LOG_SEVERITY_ERROR, py_args);
}


// Sets a new breakpoint in Python code. The breakpoint may have an optional
// condition to evaluate. When the breakpoint hits (and the condition matches)
// a callable object will be invoked from that thread.
//
// The breakpoint doesn't expire automatically after hit. It is the
// responsibility of the caller to call "ClearConditionalBreakpoint"
// appropriately.
//
// Args:
//   code_object: Python code object to set the breakpoint.
//   line: line number to set the breakpoint.
//   condition: optional callable object representing the condition to evaluate
//       or None for an unconditional breakpoint.
//   callback: callable object to invoke on breakpoint event. The callable is
//       invoked with two arguments: (event, frame). See "BreakpointFn" for more
//       details.
//
// Returns:
//   Integer cookie identifying this breakpoint. It needs to be specified when
//   clearing the breakpoint.
static PyObject* SetConditionalBreakpoint(PyObject* self, PyObject* py_args) {
  PyCodeObject* code_object = nullptr;
  int line = -1;
  PyCodeObject* condition = nullptr;
  PyObject* callback = nullptr;
  if (!PyArg_ParseTuple(py_args, "OiOO",
                        &code_object, &line, &condition, &callback)) {
    return nullptr;
  }

  if ((code_object == nullptr) || !PyCode_Check(code_object)) {
    PyErr_SetString(PyExc_TypeError, "invalid code_object argument");
    return nullptr;
  }

  if ((callback == nullptr) || !PyCallable_Check(callback)) {
    PyErr_SetString(PyExc_TypeError, "callback must be a callable object");
    return nullptr;
  }

  if (reinterpret_cast<PyObject*>(condition) == Py_None) {
    condition = nullptr;
  }

  if ((condition != nullptr) && !PyCode_Check(condition)) {
    PyErr_SetString(
        PyExc_TypeError,
        "condition must be None or a code object");
    return nullptr;
  }

  // Rate limiting has to be initialized before it is used for the first time.
  // We can't initialize it on module start because it happens before the
  // command line is parsed and flags are still at their default values.
  LazyInitializeRateLimit();

  auto conditional_breakpoint = std::make_shared<ConditionalBreakpoint>(
      ScopedPyCodeObject::NewReference(condition),
      ScopedPyObject::NewReference(callback));

  int cookie = -1;

  cookie = g_bytecode_breakpoint.SetBreakpoint(
      code_object,
      line,
      std::bind(
          &ConditionalBreakpoint::OnBreakpointHit,
          conditional_breakpoint),
      std::bind(
          &ConditionalBreakpoint::OnBreakpointError,
          conditional_breakpoint));
  if (cookie == -1) {
    conditional_breakpoint->OnBreakpointError();
  }

  return PyInt_FromLong(cookie);
}


// Clears the breakpoint previously set by "SetConditionalBreakpoint". Must be
// called exactly once per each call to "SetConditionalBreakpoint".
//
// Args:
//   cookie: breakpoint identifier returned by "SetConditionalBreakpoint".
static PyObject* ClearConditionalBreakpoint(PyObject* self, PyObject* py_args) {
  int cookie = -1;
  if (!PyArg_ParseTuple(py_args, "i", &cookie)) {
    return nullptr;
  }

  g_bytecode_breakpoint.ClearBreakpoint(cookie);

  Py_RETURN_NONE;
}


// Invokes a Python callable object with immutability tracer.
//
// This ensures that the called method doesn't change any state, doesn't call
// unsafe native functions and doesn't take unreasonable amount of time to
// complete.
//
// This method supports multiple arguments to be specified. If no arguments
// needed, the caller should specify an empty tuple.
//
// Args:
//   frame: defines the evaluation context.
//   code: code object to invoke.
//
// Returns:
//   Return value of the callable.
static PyObject* CallImmutable(PyObject* self, PyObject* py_args) {
  PyObject* obj_frame = nullptr;
  PyObject* obj_code = nullptr;
  if (!PyArg_ParseTuple(py_args, "OO", &obj_frame, &obj_code)) {
    return nullptr;
  }

  if (!PyFrame_Check(obj_frame)) {
    PyErr_SetString(PyExc_TypeError, "argument 1 must be a frame object");
    return nullptr;
  }

  if (!PyCode_Check(obj_code)) {
    PyErr_SetString(PyExc_TypeError, "argument 2 must be a code object");
    return nullptr;
  }

  PyFrameObject* frame = reinterpret_cast<PyFrameObject*>(obj_frame);

  PyFrame_FastToLocals(frame);

  ScopedImmutabilityTracer immutability_tracer;
#if PY_MAJOR_VERSION >= 3
  return PyEval_EvalCode(obj_code, frame->f_globals, frame->f_locals);
#else
  return PyEval_EvalCode(reinterpret_cast<PyCodeObject*>(obj_code),
                         frame->f_globals, frame->f_locals);
#endif
}

// Applies the dynamic logs quota, which is limited by both total messages and
// total bytes. This should be called before doing the actual logging call.
//
// Args:
//   num_bytes: number of bytes in the message to log.
// Returns:
//   True if there is quota available, False otherwise.
static PyObject* ApplyDynamicLogsQuota(PyObject* self, PyObject* py_args) {
  LazyInitializeRateLimit();
  int num_bytes = -1;
  if (!PyArg_ParseTuple(py_args, "i", &num_bytes) || num_bytes < 1) {
    Py_RETURN_FALSE;
  }

  LeakyBucket* global_dynamic_log_limiter = GetGlobalDynamicLogQuota();
  LeakyBucket* global_dynamic_log_bytes_limiter =
      GetGlobalDynamicLogBytesQuota();

  if (global_dynamic_log_limiter->RequestTokens(1) &&
      global_dynamic_log_bytes_limiter->RequestTokens(num_bytes)) {
    Py_RETURN_TRUE;
  } else {
    Py_RETURN_FALSE;
  }
}

static PyMethodDef g_module_functions[] = {
  {
    "InitializeModule",
    InitializeModule,
    METH_VARARGS,
    "Initialize C++ flags and logging."
  },
  {
    "LogInfo",
    LogInfo,
    METH_VARARGS,
    "INFO level logging from Python code."
  },
  {
    "LogWarning",
    LogWarning,
    METH_VARARGS,
    "WARNING level logging from Python code."
  },
  {
    "LogError",
    LogError,
    METH_VARARGS,
    "ERROR level logging from Python code."
  },
  {
    "SetConditionalBreakpoint",
    SetConditionalBreakpoint,
    METH_VARARGS,
    "Sets a new breakpoint in Python code."
  },
  {
    "ClearConditionalBreakpoint",
    ClearConditionalBreakpoint,
    METH_VARARGS,
    "Clears previously set breakpoint in Python code."
  },
  {
    "CallImmutable",
    CallImmutable,
    METH_VARARGS,
    "Invokes a Python callable object with immutability tracer."
  },
  {
    "ApplyDynamicLogsQuota",
    ApplyDynamicLogsQuota,
    METH_VARARGS,
    "Applies the dynamic log quota"
  },
  { nullptr, nullptr, 0, nullptr }  // sentinel
};


#if PY_MAJOR_VERSION >= 3
static struct PyModuleDef moduledef = {
  PyModuleDef_HEAD_INIT, /** m_base */
  CDBG_MODULE_NAME, /** m_name */
  "Native module for Python Cloud Debugger", /** m_doc */
  -1, /** m_size */
  g_module_functions, /** m_methods */
  NULL, /** m_slots */
  NULL, /** m_traverse */
  NULL, /** m_clear */
  NULL /** m_free */
};

PyObject* InitDebuggerNativeModuleInternal() {
  PyObject* module = PyModule_Create(&moduledef);
#else
PyObject* InitDebuggerNativeModuleInternal() {
  PyObject* module = Py_InitModule3(
      CDBG_MODULE_NAME,
      g_module_functions,
      "Native module for Python Cloud Debugger");
#endif

  SetDebugletModule(module);

  if (!RegisterPythonType<PythonCallback>() ||
      !RegisterPythonType<ImmutabilityTracer>()) {
    return nullptr;
  }

  // Add constants we want to share with the Python code.
  for (uint32 i = 0; i < arraysize(kIntegerConstants); ++i) {
    if (PyModule_AddObject(
          module,
          kIntegerConstants[i].name,
          PyInt_FromLong(kIntegerConstants[i].value))) {
      LOG(ERROR) << "Failed to constant " << kIntegerConstants[i].name
                 << " to native module";
      return nullptr;
    }
  }

  return module;
}

void InitDebuggerNativeModule() {
  InitDebuggerNativeModuleInternal();
}

}  // namespace cdbg
}  // namespace devtools


// This function is called to initialize the module.
#if PY_MAJOR_VERSION >= 3
PyMODINIT_FUNC PyInit_cdbg_native() {
  return devtools::cdbg::InitDebuggerNativeModuleInternal();
}
#else
PyMODINIT_FUNC initcdbg_native() {
  devtools::cdbg::InitDebuggerNativeModule();
}
#endif
