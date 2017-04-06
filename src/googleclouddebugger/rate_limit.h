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

#ifndef DEVTOOLS_CDBG_DEBUGLETS_PYTHON_RATE_LIMIT_H_
#define DEVTOOLS_CDBG_DEBUGLETS_PYTHON_RATE_LIMIT_H_

#include <memory>
#include "leaky_bucket.h"
#include "common.h"

namespace devtools {
namespace cdbg {

// Initializes quota objects if not initialized yet.
void LazyInitializeRateLimit();

// Release quota objects.
void CleanupRateLimit();

// Condition and dynamic logging rate limits are defined as the maximum
// number of lines of Python code per second to execute. These rate are enforced
// as following:
// 1. If a single breakpoint contributes to half the maximum rate, that
//    breakpoint will be deactivated.
// 2. If all breakpoints combined hit the maximum rate, any breakpoint to
//    exceed the limit gets disabled.
//
// The first rule ensures that in vast majority of scenarios expensive
// breakpoints will get deactivated. The second rule guarantees that in edge
// case scenarios the total amount of time spent in condition evaluation will
// not exceed the alotted limit.
//
// While the actual cost of Python lines is not uniform, we only care about the
// average. All limits ignore the number of CPUs since Python is inherently
// single threaded.
LeakyBucket* GetGlobalConditionQuota();
std::unique_ptr<LeakyBucket> CreatePerBreakpointConditionQuota();
LeakyBucket* GetGlobalDynamicLogQuota();
LeakyBucket* GetGlobalDynamicLogBytesQuota();
}  // namespace cdbg
}  // namespace devtools

#endif  // DEVTOOLS_CDBG_DEBUGLETS_PYTHON_RATE_LIMIT_H_
