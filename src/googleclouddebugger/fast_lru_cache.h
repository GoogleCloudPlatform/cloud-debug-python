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

#ifndef DEVTOOLS_CDBG_DEBUGLETS_PYTHON_FAST_LRU_CACHE_H_
#define DEVTOOLS_CDBG_DEBUGLETS_PYTHON_FAST_LRU_CACHE_H_

#include <time.h>
#include <limits>
#include "common.h"
#include "nullable.h"

namespace devtools {
namespace cdbg {

// Very small and fast LRU cache. The complexity of lookup is linear with
// cache size. This is only efficient for very small cache sizes.
//
// This class is not thread safe.
template <
    typename TKey,
    typename TValue,
    int Size = 16>
class FastLRUCache {
 public:
  FastLRUCache() {}

  void Set(TKey key, TValue value) {
    int least_used_index = 0;
    clock_t least_used_time = std::numeric_limits<clock_t>::max();
    for (int i = 0; i < Size; ++i) {
      if (cache_[i].is_valid && (cache_[i].key == key)) {
        cache_[i].value = value;
        cache_[i].last_used_time = clock();
        return;
      }

      if (!cache_[i].is_valid) {
        cache_[i].is_valid = true;
        cache_[i].key = key;
        cache_[i].value = value;
        cache_[i].last_used_time = clock();
        return;
      }

      if (cache_[i].last_used_time < least_used_time) {
        least_used_index = i;
        least_used_time = cache_[i].last_used_time;
      }
    }

    cache_[least_used_index].key = key;
    cache_[least_used_index].value = value;
    cache_[least_used_index].last_used_time = clock();
  }

  template <typename TLookup>
  Nullable<TValue> Get(TLookup key) {
    for (uint32 i = 0; i < arraysize(cache_); ++i) {
      if (cache_[i].is_valid && (cache_[i].key == key)) {
        cache_[i].last_used_time = clock();
        return Nullable<TValue>(cache_[i].value);
      }
    }

    return Nullable<TValue>();  // return nullptr.
  }

  void Reset() {
    for (uint32 i = 0; i < arraysize(cache_); ++i) {
      cache_[i].is_valid = false;
      cache_[i].key = TKey();
      cache_[i].value = TValue();
    }
  }

 private:
  struct Item {
    bool is_valid;
    TKey key;
    TValue value;
    clock_t last_used_time;
  };

  Item cache_[Size];

  DISALLOW_COPY_AND_ASSIGN(FastLRUCache);
};

}  // namespace cdbg
}  // namespace devtools

#endif  // DEVTOOLS_CDBG_DEBUGLETS_PYTHON_FAST_LRU_CACHE_H_
