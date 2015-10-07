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

#include "thread_breakpoints.h"

#include "rate_limit.h"

namespace devtools {
namespace cdbg {

// Disables the functionality of BreakpointsEmulator in the current
// native thread (which might not be the same as Python thread).
static __thread int g_threadDisableThreadBreakpoints = 0;


ScopedThreadDisableThreadBreakpoints::ScopedThreadDisableThreadBreakpoints() {
  ++g_threadDisableThreadBreakpoints;
}


ScopedThreadDisableThreadBreakpoints::~ScopedThreadDisableThreadBreakpoints() {
  --g_threadDisableThreadBreakpoints;
}


PyTypeObject ThreadBreakpoints::python_type_ =
    DefaultTypeDefinition(CDBG_SCOPED_NAME("_ThreadBreakpoints"));


ThreadBreakpoints::ThreadBreakpoints()
    : self_(nullptr),
      profile_active_(false),
      trace_active_(false),
      in_callback_(false) {
}


ThreadBreakpoints::~ThreadBreakpoints() {
  // At this point we don't even know if our thread is alive, so don't
  // even try cleanup. The owner should call DetachThread when debugger
  // goes down.
}


void ThreadBreakpoints::Initialize(PyObject* self) {
  self_ = self;
}


void ThreadBreakpoints::DetachThread() {
  breakpoints_.clear();
  ActiveBreakpointsChanged();
}


void ThreadBreakpoints::SetBreakpoint(
    const PythonBreakpoint& new_breakpoint) {
  breakpoints_.push_back(new_breakpoint);

  ActiveBreakpointsChanged();
}


void ThreadBreakpoints::ClearBreakpoint(int cookie) {
  // TODO(vlif): clearing all breakpoints incur O(n^2) complexity
  // here. Need a better data structure to support >100 breakpoints.
  for (auto it = breakpoints_.begin(); it != breakpoints_.end(); ) {
    if (it->cookie == cookie) {
      it = breakpoints_.erase(it);
    } else {
      ++it;
    }
  }

  ActiveBreakpointsChanged();
}


void ThreadBreakpoints::RebuildLineMap() {
  line_map_.clear();

  for (auto it = breakpoints_.cbegin(); it != breakpoints_.cend(); ++it) {
    line_map_[it->source_line].push_back(it);
  }
}


void ThreadBreakpoints::ActiveBreakpointsChanged() {
  RebuildLineMap();
  is_breakpoint_at_code_object_cache_.Reset();

  if (!in_callback_) {
    if (!breakpoints_.empty() && !profile_active_ && !trace_active_) {
      EnableProfileCallback(true);
    }

    if (breakpoints_.empty()) {
      EnableProfileCallback(false);
      EnableTraceCallback(false);
    }
  }
}


void ThreadBreakpoints::EnableProfileCallback(bool enable) {
  if (enable) {
    if (!profile_active_) {
      PyEval_SetProfile(OnTraceCallback, self_);
      profile_active_ = true;
    }
  } else {
    if (profile_active_) {
      PyEval_SetProfile(nullptr, nullptr);
      profile_active_ = false;
    }
  }
}


void ThreadBreakpoints::EnableTraceCallback(bool enable) {
  if (enable) {
    if (!trace_active_) {
      PyEval_SetTrace(OnTraceCallback, self_);
      trace_active_ = true;
    }
  } else {
    if (trace_active_) {
      PyEval_SetTrace(nullptr, nullptr);
      trace_active_ = false;
    }
  }
}


int ThreadBreakpoints::OnTraceCallbackInternal(
    PyFrameObject* frame,
    int what,
    PyObject* arg) {
  DCHECK(!in_callback_);
  DCHECK(!breakpoints_.empty());
  DCHECK_GE(g_threadDisableThreadBreakpoints, 0);

  if (g_threadDisableThreadBreakpoints > 0) {
    return 0;
  }

  if (!GetTraceQuota()->RequestTokens(1)) {
    std::vector<PythonBreakpoint> copy = breakpoints_;
    for (auto it = copy.begin(); it != copy.end(); ++it) {
      it->callback(BreakpointEvent::EmulatorQuotaExceeded, nullptr);
    }
  }

  switch (what) {
    case PyTrace_CALL: {
      bool breakpoint_at_code_object = IsBreakpointAtCodeObject(frame->f_code);

      if (trace_active_ && !breakpoint_at_code_object) {
        // Entering function without breakpoint. Line trace can be disabled
        // now, but we need profile callback to enable the tracer when the
        // execution returns to a function with a breakpoint.
        EnableTraceCallback(false);
        EnableProfileCallback(true);
      }

      if (!trace_active_ && breakpoint_at_code_object) {
        // Entering function with breakpoint. Line trace needs to be
        // enabled now. Since line trace is a superset of profile, we can
        // disable the later for performance reasons.
        EnableTraceCallback(true);
        EnableProfileCallback(false);
      }
      return 0;
    }

    case PyTrace_EXCEPTION:
      return 0;

    case PyTrace_LINE: {
      auto it = line_map_.find(frame->f_lineno);
      if (it == line_map_.end()) {
        return 0;
      }

      // We can only get to "PyTrace_LINE" with line tracer. When it is
      // enable, profiler is always disabled.
      DCHECK(!profile_active_);

      std::vector<BreakpointFn> matches;
      for (auto bp = it->second.begin(); bp != it->second.end(); ++bp) {
        if (frame->f_code == (*bp)->code_object.get()) {
          matches.push_back((*bp)->callback);
        }
      }

      if (!matches.empty()) {
        // Disable all trace functions before invoking callback. This
        // callback will go into Python code. Any expression evaluation
        // invoked at breakpoint handler will also reset the tracer.
        // Better just disable it here and enable it when we are done.
        EnableTraceCallback(false);

        in_callback_ = true;

        for (auto cb = matches.begin(); cb != matches.end(); ++cb) {
          (*cb)(BreakpointEvent::Hit, frame);
        }

        DCHECK(!trace_active_);
        DCHECK(!profile_active_);
        DCHECK(in_callback_);

        in_callback_ = false;

        if (IsBreakpointAtCodeObject(frame->f_code)) {
          EnableTraceCallback(true);
        } else if (!breakpoints_.empty()) {
          EnableProfileCallback(true);
        }
      }

      return 0;
    }

    case PyTrace_RETURN: {
      PyFrameObject* previous_frame = frame->f_back;
      if (!trace_active_ &&
          (previous_frame != nullptr) &&
          IsBreakpointAtCodeObject(previous_frame->f_code)) {
        // Returning to a function with breakpoint. Line trace needs to be
        // enabled now. Since line trace is a superset of profile, we can
        // disable the later for performance reasons.
        EnableTraceCallback(true);
        EnableProfileCallback(false);
      }

      return 0;
    }

    case PyTrace_C_CALL:
      return 0;

    case PyTrace_C_EXCEPTION:
      return 0;

    case PyTrace_C_RETURN:
      return 0;

    default:
      return 0;
  }
}


bool ThreadBreakpoints::IsBreakpointAtCodeObject(
    PyCodeObject* code_object) {
  Nullable<bool> cached_result =
      is_breakpoint_at_code_object_cache_.Get(code_object);
  if (cached_result.has_value()) {
    return cached_result.value();
  }

  bool rc = false;
  CodeObjectLinesEnumerator enumerator(code_object);
  do {
    auto it = line_map_.find(enumerator.line_number());
    if (it == line_map_.end()) {
      continue;
    }

    for (auto bp = it->second.begin(); bp != it->second.end(); ++bp) {
      if (code_object == (*bp)->code_object.get()) {
        rc = true;
        break;
      }
    }
  } while (enumerator.Next() && !rc);

  is_breakpoint_at_code_object_cache_.Set(
      ScopedPyCodeObject::NewReference(code_object),
      rc);

  return rc;
}

}  // namespace cdbg
}  // namespace devtools


