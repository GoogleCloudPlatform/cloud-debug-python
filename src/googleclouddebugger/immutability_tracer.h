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

#ifndef DEVTOOLS_CDBG_DEBUGLETS_PYTHON_IMMUTABILITY_TRACER_H_
#define DEVTOOLS_CDBG_DEBUGLETS_PYTHON_IMMUTABILITY_TRACER_H_

#include <unordered_set>
#include "common.h"
#include "python_util.h"

namespace devtools {
namespace cdbg {

// Uses Python line tracer to track evaluation of Python expression. As the
// evaluation progresses, verifies that no opcodes with side effect are
// executed.
//
// Execution of code with side effects will be blocked and exception will
// be thrown.
//
// This class is not thread safe. All the functions assume Interpreter Lock
// held by the current thread.
//
// This class resets tracer ("PyEval_SetTrace") in destructor. It does not
// restore the previous one (because such Python does not provide such API).
// It is up to the caller to reset the tracer.
class ImmutabilityTracer {
 public:
  ImmutabilityTracer();

  ~ImmutabilityTracer();

  // Starts immutability tracer on the current thread.
  void Start(PyObject* self);

  // Stops immutability tracer on the current thread.
  void Stop();

  // Returns true if the expression wasn't completely executed because of
  // a mutable code.
  bool IsMutableCodeDetected() const { return mutable_code_detected_; }

  // Gets the number of lines executed while the tracer was enabled. Native
  // functions calls are counted as a single line.
  int32 GetLineCount() const { return line_count_; }

 private:
  // Python tracer callback function.
  static int OnTraceCallback(
      PyObject* obj,
      PyFrameObject* frame,
      int what,
      PyObject* arg) {
    auto* instance = py_object_cast<ImmutabilityTracer>(obj);
    return instance->OnTraceCallbackInternal(frame, what, arg);
  }

  // Python tracer callback function (instance function for convenience).
  int OnTraceCallbackInternal(PyFrameObject* frame, int what, PyObject* arg);

  // Verifies that the code object doesn't include calls to blocked primitives.
  void VerifyCodeObject(ScopedPyCodeObject code_object);

  // Verifies immutability of code on a single line.
  void ProcessCodeLine(PyCodeObject* code_object, int line_number);

  // Verifies immutability of block of opcodes.
  void ProcessCodeRange(const uint8* code_start, const uint8* opcodes,
                        int size);

  // Verifies that the called C function is whitelisted.
  void ProcessCCall(PyObject* function);

  // Sets an exception indicating that the code is mutable.
  void SetMutableCodeException();

 public:
  // Definition of Python type object.
  static PyTypeObject python_type_;

 private:
  // Weak reference to Python object wrapping this class.
  PyObject* self_;

  // Evaluation thread.
  PyThreadState* thread_state_;

  // Set of code object verified to not have any blocked primitives.
  std::unordered_set<
      ScopedPyCodeObject,
      ScopedPyCodeObject::Hash> verified_code_objects_;

  // Original value of PyThreadState::tracing. We revert it to 0 to enforce
  // trace callback on this thread, even if the whole thing was executed from
  // within another trace callback (that caught the breakpoint).
  int32 original_thread_state_tracing_;

  // Counts the number of lines executed while the tracer was enabled. Native
  // functions calls are counted as a single line.
  int32 line_count_;

  // Set to true after immutable statement is detected. When it happens we
  // want to stop execution of the entire construct entirely.
  bool mutable_code_detected_;

  DISALLOW_COPY_AND_ASSIGN(ImmutabilityTracer);
};

// Creates and initializes instance of "ImmutabilityTracer" in constructor and
// stops the tracer in destructor.
//
// This class assumes Interpreter Lock held by the current thread throughout
// its lifetime.
class ScopedImmutabilityTracer {
 public:
  ScopedImmutabilityTracer()
      : tracer_(NewNativePythonObject<ImmutabilityTracer>()) {
    Instance()->Start(tracer_.get());
  }

  ~ScopedImmutabilityTracer() {
    Instance()->Stop();
  }

  // Returns true if the expression wasn't completely executed because of
  // a mutable code.
  bool IsMutableCodeDetected() const {
    return Instance()->IsMutableCodeDetected();
  }

  // Gets the number of lines executed while the tracer was enabled. Native
  // functions calls are counted as a single line.
  int32 GetLineCount() const { return Instance()->GetLineCount(); }

 private:
  ImmutabilityTracer* Instance() {
    return py_object_cast<ImmutabilityTracer>(tracer_.get());
  }

  const ImmutabilityTracer* Instance() const {
    return py_object_cast<ImmutabilityTracer>(tracer_.get());
  }

 private:
  const ScopedPyObject tracer_;

  DISALLOW_COPY_AND_ASSIGN(ScopedImmutabilityTracer);
};

}  // namespace cdbg
}  // namespace devtools

#endif  // DEVTOOLS_CDBG_DEBUGLETS_PYTHON_IMMUTABILITY_TRACER_H_
