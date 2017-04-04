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

#include "rate_limit.h"

DEFINE_int32(
    max_condition_lines_rate,
    5000,
    "maximum number of Python lines/sec to spend on condition evaluation");

namespace devtools {
namespace cdbg {

// Define capacity of leaky bucket:
//   capacity = fill_rate * capacity_factor
//
// The capacity is conceptually unrelated to fill rate, but we don't want to
// expose this knob to the developers. Defining it as a factor of a fill rate
// is a convinient heuristics.
//
// Smaller factor values ensure that a burst of CPU consumption due to the
// debugger wil not impact the service throughput. Longer values will allow the
// burst, and will only disable the breakpoint if CPU consumption due to
// debugger is continuous for a prolonged period of time.
static const double kConditionCostCapacityFactor = 0.1;

static std::unique_ptr<LeakyBucket> g_global_condition_quota;


static int64 GetBaseConditionQuotaCapacity() {
  return FLAGS_max_condition_lines_rate * kConditionCostCapacityFactor;
}


void LazyInitializeRateLimit() {
  if (g_global_condition_quota == nullptr) {
    g_global_condition_quota.reset(new LeakyBucket(
        GetBaseConditionQuotaCapacity(),
        FLAGS_max_condition_lines_rate));
  }
}


void CleanupRateLimit() {
  g_global_condition_quota = nullptr;
}


LeakyBucket* GetGlobalConditionQuota() {
  return g_global_condition_quota.get();
}


std::unique_ptr<LeakyBucket> CreatePerBreakpointConditionQuota() {
  return std::unique_ptr<LeakyBucket>(new LeakyBucket(
      GetBaseConditionQuotaCapacity() / 2,
      FLAGS_max_condition_lines_rate / 2));
}

}  // namespace cdbg
}  // namespace devtools
