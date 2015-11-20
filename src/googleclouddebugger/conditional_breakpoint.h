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

#ifndef DEVTOOLS_CDBG_DEBUGLETS_PYTHON_CONDITIONAL_BREAKPOINT_H_
#define DEVTOOLS_CDBG_DEBUGLETS_PYTHON_CONDITIONAL_BREAKPOINT_H_

#include "leaky_bucket.h"
#include "common.h"
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

  // Evaluation of conditional expression is consuming too much resources. It is
  // a responsibility of the next layer to disable the offending breakpoint.
  GlobalConditionQuotaExceeded,
  BreakpointConditionQuotaExceeded,

  // The conditional expression changes state of the program and therefore not
  // allowed.
  ConditionExpressionMutable,
};


// Implements breakpoint action to evaluate optional breakpoint condition. If
// the condition matches, calls Python callable object.
class ConditionalBreakpoint {
 public:
  ConditionalBreakpoint(ScopedPyCodeObject condition, ScopedPyObject callback);

  ~ConditionalBreakpoint();

  void OnBreakpointHit();

  void OnBreakpointError();

 private:
  // Evaluates breakpoint condition within the context of the specified frame.
  // Returns true if the breakpoint doesn't have condition or if condition
  // was evaluated to True. Otherwise returns false. Raised exceptions are
  // considered as condition not matched.
  bool EvaluateCondition(PyFrameObject* frame);

  // Takes "time_ns" tokens from the quota for CPU consumption due to breakpoint
  // condition. If the quota is exceeded, this function clears the breakpoint
  // and reports "ConditionQuotaExceeded" breakpoint event.
  void ApplyConditionQuota(int time_ns);

  // Notifies the next layer through the callable object.
  void NotifyBreakpointEvent(BreakpointEvent event, PyFrameObject* frame);

 private:
  // Callable object representing the compiled conditional expression to
  // evaluate on each breakpoint hit. If the breakpoint has no condition, this
  // field will be nullptr.
  ScopedPyCodeObject condition_;

  // Python callable object to invoke on breakpoint events.
  ScopedPyObject python_callback_;

  // Per breakpoint quota on cost of evaluating breakpoint conditions. See
  // "rate_limit.h" file for detailed explanation.
  std::unique_ptr<LeakyBucket> per_breakpoint_condition_quota_;

  DISALLOW_COPY_AND_ASSIGN(ConditionalBreakpoint);
};

}  // namespace cdbg
}  // namespace devtools

#endif  // DEVTOOLS_CDBG_DEBUGLETS_PYTHON_CONDITIONAL_BREAKPOINT_H_
