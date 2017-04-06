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

DEFINE_int32(
    max_dynamic_log_rate,
    50,  // maximum of 50 log entries per second on average
    "maximum rate of dynamic log entries in this process; short bursts are "
    "allowed to exceed this limit");

DEFINE_int32(
    max_dynamic_log_bytes_rate,
    20480,  // maximum of 20K bytes per second on average
    "maximum rate of dynamic log bytes in this process; short bursts are "
    "allowed to exceed this limit");

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
static const double kDynamicLogCapacityFactor = 5;
static const double kDynamicLogBytesCapacityFactor = 2;

static std::unique_ptr<LeakyBucket> g_global_condition_quota;
static std::unique_ptr<LeakyBucket> g_global_dynamic_log_quota;
static std::unique_ptr<LeakyBucket> g_global_dynamic_log_bytes_quota;


static int64 GetBaseConditionQuotaCapacity() {
  return FLAGS_max_condition_lines_rate * kConditionCostCapacityFactor;
}

void LazyInitializeRateLimit() {
  if (g_global_condition_quota == nullptr) {
    g_global_condition_quota.reset(new LeakyBucket(
        GetBaseConditionQuotaCapacity(),
        FLAGS_max_condition_lines_rate));

    g_global_dynamic_log_quota.reset(new LeakyBucket(
        FLAGS_max_dynamic_log_rate * kDynamicLogCapacityFactor,
        FLAGS_max_dynamic_log_rate));

    g_global_dynamic_log_bytes_quota.reset(new LeakyBucket(
        FLAGS_max_dynamic_log_bytes_rate * kDynamicLogBytesCapacityFactor,
        FLAGS_max_dynamic_log_bytes_rate));
  }
}


void CleanupRateLimit() {
  g_global_condition_quota = nullptr;
  g_global_dynamic_log_quota = nullptr;
  g_global_dynamic_log_bytes_quota = nullptr;
}


LeakyBucket* GetGlobalConditionQuota() {
  return g_global_condition_quota.get();
}

LeakyBucket* GetGlobalDynamicLogQuota() {
  return g_global_dynamic_log_quota.get();
}

LeakyBucket* GetGlobalDynamicLogBytesQuota() {
  return g_global_dynamic_log_bytes_quota.get();
}

std::unique_ptr<LeakyBucket> CreatePerBreakpointConditionQuota() {
  return std::unique_ptr<LeakyBucket>(new LeakyBucket(
      GetBaseConditionQuotaCapacity() / 2,
      FLAGS_max_condition_lines_rate / 2));
}

}  // namespace cdbg
}  // namespace devtools
