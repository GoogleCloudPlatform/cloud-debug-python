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

#ifndef DEVTOOLS_CDBG_DEBUGLETS_PYTHON_BYTECODE_MANIPULATOR_H_
#define DEVTOOLS_CDBG_DEBUGLETS_PYTHON_BYTECODE_MANIPULATOR_H_

#include <vector>
#include "common.h"

namespace devtools {
namespace cdbg {

// Inserts breakpoint method calls into bytecode of Python method.
//
// By default new instructions are inserted into the bytecode. When this
// happens, all other branch instructions need to be adjusted.
// For example consider this Python code:
//     def test():
//       return 'hello'
// It's bytecode without any breakpoints is:
//      0 LOAD_CONST               1 ('hello')
//      3 RETURN_VALUE
// The transformed bytecode with a breakpoint set at "print 'After'" line:
//      0 LOAD_CONST               2 (cdbg_native._Callback)
//      3 CALL_FUNCTION            0
//      6 POP_TOP
//      7 LOAD_CONST               1 ('hello')
//     10 RETURN_VALUE
//
// Special care is given to generator methods. These are methods that use
// yield statement that translates to YIELD_VALUE. Built-in generator class
// keeps the Python frame around in between the calls. The frame stores
// the offset of the instruction to return in "f_lasti". This offset has to
// stay valid, even if the breakpoint is set or cleared in between calls to the
// generator function. To achieve this the breakpoint code is appended to the
// end of the method instead of the default insertion.
// For example consider this Python code:
//     def test():
//       yield 'hello'
// Its bytecode without any breakpoints is:
//      0 LOAD_CONST               1 ('hello')
//      3 YIELD_VALUE
//      4 POP_TOP
//      5 LOAD_CONST               0 (None)
//      8 RETURN_VALUE
// When setting a breakpoint in the "yield" line, the bytecode is transformed:
//      0 JUMP_ABSOLUTE            9
//      3 YIELD_VALUE
//      4 POP_TOP
//      5 LOAD_CONST               0 (None)
//      8 RETURN_VALUE
//      9 LOAD_CONST               2 (cdbg_native._Callback)
//     12 CALL_FUNCTION            0
//     15 POP_TOP
//     16 LOAD_CONST               1 ('hello')
//     19 JUMP_ABSOLUTE            3
class BytecodeManipulator {
 public:
  BytecodeManipulator(
      std::vector<uint8> bytecode,
      const bool has_lnotab,
      std::vector<uint8> lnotab);

  // Gets the transformed method bytecode.
  const std::vector<uint8>& bytecode() const { return data_.bytecode; }

  // Returns true if this class was initialized with line numbers table.
  bool has_lnotab() const { return has_lnotab_; }

  // Gets the method line numbers table or empty vector if not available.
  const std::vector<uint8>& lnotab() const { return data_.lnotab; }

  // Rewrites the method bytecode to invoke callable at the specified offset.
  // Return false if the method call could not be inserted. The bytecode
  // is not affected.
  bool InjectMethodCall(int offset, int callable_const_index);

 private:
  // Algorithm to insert breakpoint callback into method bytecode.
  enum Strategy {
    // Fail any attempts to set a breakpoint in this method.
    STRATEGY_FAIL,

    // Inserts method call instruction right into the method bytecode. This
    // strategy works for all possible locations, but can't be used in
    // generators (i.e. methods that use "yield").
    STRATEGY_INSERT,

    // Appends method call instruction at the end of the method bytecode. This
    // strategy works for generators (i.e. methods that use "yield"). The bad
    // news is that breakpoints can't be set in all locations.
    STRATEGY_APPEND
  };

  struct Data {
    // Bytecode of a transformed method.
    std::vector<uint8> bytecode;

    // Method line numbers table or empty vector if "has_lnotab_" is false.
    std::vector<uint8> lnotab;
  };

  // Insert space into the bytecode. This space is later used to add new
  // instructions.
  bool InsertSpace(Data* data, int offset, int size) const;

  // Injects a method call using STRATEGY_INSERT on a temporary copy of "Data"
  // that can be dropped in case of a failure.
  bool InsertMethodCall(Data* data, int offset, int const_index) const;

  // Injects a method call using STRATEGY_APPEND on a temporary copy of "Data"
  // that can be dropped in case of a failure.
  bool AppendMethodCall(Data* data, int offset, int const_index) const;

 private:
  // Method bytecode and line number table.
  Data data_;

  // True if the method has line number table.
  const bool has_lnotab_;

  // Algorithm to insert breakpoint callback into method bytecode.
  Strategy strategy_;

  DISALLOW_COPY_AND_ASSIGN(BytecodeManipulator);
};

}  // namespace cdbg
}  // namespace devtools

#endif  // DEVTOOLS_CDBG_DEBUGLETS_PYTHON_BYTECODE_MANIPULATOR_H_
