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

#include "conditional_breakpoint.h"

#include "immutability_tracer.h"
#include "rate_limit.h"

namespace devtools {
namespace cdbg {

ConditionalBreakpoint::ConditionalBreakpoint(
    ScopedPyCodeObject condition,
    ScopedPyObject callback)
    : condition_(condition),
      python_callback_(callback),
      per_breakpoint_condition_quota_(CreatePerBreakpointConditionQuota()) {
}


ConditionalBreakpoint::~ConditionalBreakpoint() {
}



void ConditionalBreakpoint::OnBreakpointHit() {
  PyFrameObject* frame = PyThreadState_Get()->frame;

  if (!EvaluateCondition(frame)) {
    return;
  }

  NotifyBreakpointEvent(BreakpointEvent::Hit, frame);
}


void ConditionalBreakpoint::OnBreakpointError() {
  NotifyBreakpointEvent(BreakpointEvent::Error, nullptr);
}


bool ConditionalBreakpoint::EvaluateCondition(PyFrameObject* frame) {
  if (condition_ == nullptr) {
    return true;
  }

  PyFrame_FastToLocals(frame);

  ScopedPyObject result;
  bool is_mutable_code_detected = false;
  int32 line_count = 0;

  {
    ScopedImmutabilityTracer immutability_tracer;
    result.reset(PyEval_EvalCode(
#if PY_MAJOR_VERSION >= 3
        reinterpret_cast<PyObject*>(condition_.get()),
#else
        condition_.get(),
#endif
        frame->f_globals,
        frame->f_locals));
    is_mutable_code_detected = immutability_tracer.IsMutableCodeDetected();
    line_count = immutability_tracer.GetLineCount();
  }

  // TODO: clear breakpoint if condition evaluation failed due to
  // mutable code or timeout.

  auto eval_exception = ClearPythonException();

  if (is_mutable_code_detected) {
    NotifyBreakpointEvent(
        BreakpointEvent::ConditionExpressionMutable,
        nullptr);
    return false;
  }

  if (eval_exception.has_value()) {
    DLOG(INFO) << "Expression evaluation failed: " << eval_exception.value();
    return false;
  }

  if (PyObject_IsTrue(result.get())) {
    return true;
  }

  ApplyConditionQuota(line_count);

  return false;
}


void ConditionalBreakpoint::ApplyConditionQuota(int time_ns) {
  // Apply global cost limit.
  if (!GetGlobalConditionQuota()->RequestTokens(time_ns)) {
    LOG(INFO) << "Global condition quota exceeded";
    NotifyBreakpointEvent(
        BreakpointEvent::GlobalConditionQuotaExceeded,
        nullptr);
    return;
  }

  // Apply per-breakpoint cost limit.
  if (!per_breakpoint_condition_quota_->RequestTokens(time_ns)) {
    LOG(INFO) << "Per breakpoint condition quota exceeded";
    NotifyBreakpointEvent(
        BreakpointEvent::BreakpointConditionQuotaExceeded,
        nullptr);
    return;
  }
}


void ConditionalBreakpoint::NotifyBreakpointEvent(
    BreakpointEvent event,
    PyFrameObject* frame) {
  ScopedPyObject obj_event(PyInt_FromLong(static_cast<int>(event)));
  PyObject* obj_frame = reinterpret_cast<PyObject*>(frame) ?: Py_None;
  ScopedPyObject callback_args(PyTuple_Pack(2, obj_event.get(), obj_frame));

  ScopedPyObject result(
      PyObject_Call(python_callback_.get(), callback_args.get(), nullptr));
  ClearPythonException();
}


}  // namespace cdbg
}  // namespace devtools

