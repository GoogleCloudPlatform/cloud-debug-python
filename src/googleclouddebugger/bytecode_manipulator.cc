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

#include "bytecode_manipulator.h"

#include <algorithm>

namespace devtools {
namespace cdbg {

// Classification of Python opcodes. BRANCH_xxx_OPCODE include both branch
// opcodes (like JUMP_OFFSET) and block setup opcodes (like SETUP_EXCEPT).
enum PythonOpcodeType {
  SEQUENTIAL_OPCODE,
  BRANCH_DELTA_OPCODE,
  BRANCH_ABSOLUTE_OPCODE,
  YIELD_OPCODE
};

// Single Python instruction.
//
// In Python 2.7, there are 3 types of instructions:
// 1. Instruction without arguments (takes 1 byte).
// 2. Instruction with a single 16 bit argument (takes 3 bytes).
// 3. Instruction with a 32 bit argument (very uncommon; takes 6 bytes).
//
// In Python 3.6, there are 4 types of instructions:
// 1. Instructions without arguments, or a 8 bit argument (takes 2 bytes).
// 2. Instructions with a 16 bit argument (takes 4 bytes).
// 3. Instructions with a 24 bit argument (takes 6 bytes).
// 4. Instructions with a 32 bit argument (takes 8 bytes).
//
// To handle 32 bit arguments in Python 2, or 16-32 bit arguments in Python 3,
// a special instruction with an opcode of EXTENDED_ARG is prepended to the
// actual instruction. The argument of the EXTENDED_ARG instruction is combined
// with the argument of the next instruction to form the full argument.
struct PythonInstruction {
  uint8 opcode;
  uint32 argument;
  int size;
};

// Special pseudo-instruction to indicate failures.
static const PythonInstruction kInvalidInstruction { 0xFF, 0xFFFFFFFF,  0 };

// Creates an instance of PythonInstruction for instruction with no arguments.
static PythonInstruction PythonInstructionNoArg(uint8 opcode) {
  DCHECK(!HAS_ARG(opcode));

  PythonInstruction instruction;
  instruction.opcode = opcode;
  instruction.argument = 0;

#if PY_MAJOR_VERSION >= 3
  instruction.size = 2;
#else
  instruction.size = 1;
#endif

  return instruction;
}


// Creates an instance of PythonInstruction for instruction with an argument.
static PythonInstruction PythonInstructionArg(uint8 opcode, uint32 argument) {
  DCHECK(HAS_ARG(opcode));

  PythonInstruction instruction;
  instruction.opcode = opcode;
  instruction.argument = argument;

#if PY_MAJOR_VERSION >= 3
  if (argument <= 0xFF) {
    instruction.size = 2;
  } else if (argument <= 0xFFFF) {
    instruction.size = 4;
  } else if (argument <= 0xFFFFFF) {
    instruction.size = 6;
  } else {
    instruction.size = 8;
  }
#else
  instruction.size = instruction.argument > 0xFFFF ? 6 : 3;
#endif

  return instruction;
}


// Calculates the size of a set of instructions.
static int GetInstructionsSize(
    const std::vector<PythonInstruction>& instructions) {
  int size = 0;
  for (auto it = instructions.begin(); it != instructions.end(); ++it) {
    size += it->size;
  }

  return size;
}


// Classification of an opcode.
static PythonOpcodeType GetOpcodeType(uint8 opcode) {
  switch (opcode) {
    case YIELD_VALUE:
#if PY_MAJOR_VERSION >= 3
    case YIELD_FROM:
#endif
      return YIELD_OPCODE;

    case FOR_ITER:
    case JUMP_FORWARD:
#if PY_VERSION_HEX < 0x03080000
    // Removed in Python 3.8.
    case SETUP_LOOP:
    case SETUP_EXCEPT:
#endif
    case SETUP_FINALLY:
    case SETUP_WITH:
#if PY_VERSION_HEX >= 0x03080000
    case CALL_FINALLY:
#endif
      return BRANCH_DELTA_OPCODE;

    case JUMP_IF_FALSE_OR_POP:
    case JUMP_IF_TRUE_OR_POP:
    case JUMP_ABSOLUTE:
    case POP_JUMP_IF_FALSE:
    case POP_JUMP_IF_TRUE:
#if PY_VERSION_HEX < 0x03080000
    // Removed in Python 3.8.
    case CONTINUE_LOOP:
#endif
      return BRANCH_ABSOLUTE_OPCODE;

    default:
      return SEQUENTIAL_OPCODE;
  }
}


// Gets the target offset of a branch instruction.
static int GetBranchTarget(int offset, PythonInstruction instruction) {
  switch (GetOpcodeType(instruction.opcode)) {
    case BRANCH_DELTA_OPCODE:
      return offset + instruction.size + instruction.argument;

    case BRANCH_ABSOLUTE_OPCODE:
      return instruction.argument;

    default:
      DCHECK(false) << "Not a branch instruction";
      return -1;
  }
}


#if PY_MAJOR_VERSION < 3
// Reads 16 bit value according to Python bytecode encoding.
static uint16 ReadPythonBytecodeUInt16(std::vector<uint8>::const_iterator it) {
  return it[0] | (static_cast<uint16>(it[1]) << 8);
}


// Writes 16 bit value according to Python bytecode encoding.
static void WritePythonBytecodeUInt16(
    std::vector<uint8>::iterator it,
    uint16 data) {
  it[0] = static_cast<uint8>(data);
  it[1] = data >> 8;
}
#endif


// Read instruction at the specified offset. Returns kInvalidInstruction
// buffer underflow.
static PythonInstruction ReadInstruction(
    const std::vector<uint8>& bytecode,
    std::vector<uint8>::const_iterator it) {
  PythonInstruction instruction { 0, 0, 0 };

#if PY_MAJOR_VERSION >= 3
  if (bytecode.end() - it < 2) {
    LOG(ERROR) << "Buffer underflow";
    return kInvalidInstruction;
  }

  while (it[0] == EXTENDED_ARG) {
    instruction.argument = instruction.argument << 8 | it[1];
    it += 2;
    instruction.size += 2;
    if (bytecode.end() - it < 2) {
      LOG(ERROR) << "Buffer underflow";
      return kInvalidInstruction;
    }
  }

  instruction.opcode = it[0];
  instruction.argument = instruction.argument << 8 | it[1];
  instruction.size += 2;
#else
  if (it == bytecode.end()) {
    LOG(ERROR) << "Buffer underflow";
    return kInvalidInstruction;
  }

  instruction.opcode = it[0];
  instruction.size = 1;

  auto it_arg = it + 1;
  if (instruction.opcode == EXTENDED_ARG) {
    if (bytecode.end() - it < 6) {
      LOG(ERROR) << "Buffer underflow";
      return kInvalidInstruction;
    }

    instruction.opcode = it[3];

    auto it_ext = it + 4;
    instruction.argument =
        (static_cast<uint32>(ReadPythonBytecodeUInt16(it_arg)) << 16) |
        ReadPythonBytecodeUInt16(it_ext);
    instruction.size = 6;
  } else if (HAS_ARG(instruction.opcode)) {
    if (bytecode.end() - it < 3) {
      LOG(ERROR) << "Buffer underflow";
      return kInvalidInstruction;
    }

    instruction.argument = ReadPythonBytecodeUInt16(it_arg);
    instruction.size = 3;
  }
#endif

  return instruction;
}


// Writes instruction to the specified destination. The caller is responsible
// to make sure the target vector has enough space. Returns size of an
// instruction.
static int WriteInstruction(
    std::vector<uint8>::iterator it,
    const PythonInstruction& instruction) {
#if PY_MAJOR_VERSION >= 3
  uint32 arg = instruction.argument;
  int size_written = 0;
  // Start writing backwards from the real instruction, followed by any
  // EXTENDED_ARG instructions if needed.
  for (int i = instruction.size - 2; i >= 0; i -= 2) {
    it[i] = size_written == 0 ? instruction.opcode : EXTENDED_ARG;
    it[i + 1] = static_cast<uint8>(arg);
    arg = arg >> 8;
    size_written += 2;
  }
  return size_written;
#else
  if (instruction.size == 6) {
    it[0] = EXTENDED_ARG;
    WritePythonBytecodeUInt16(it + 1, instruction.argument >> 16);
    it[3] = instruction.opcode;
    WritePythonBytecodeUInt16(
        it + 4,
        static_cast<uint16>(instruction.argument));
    return 6;
  } else {
    it[0] = instruction.opcode;

    if (HAS_ARG(instruction.opcode)) {
      DCHECK_LE(instruction.argument, 0xFFFFU);
      WritePythonBytecodeUInt16(
          it + 1,
          static_cast<uint16>(instruction.argument));
      return 3;
    }

    return 1;
  }
#endif
}


// Write set of instructions to the specified destination.
static void WriteInstructions(
    std::vector<uint8>::iterator it,
    const std::vector<PythonInstruction>& instructions) {
  for (auto it_instruction = instructions.begin();
       it_instruction != instructions.end();
       ++it_instruction) {
    const int instruction_size = WriteInstruction(it, *it_instruction);
    DCHECK_EQ(instruction_size, it_instruction->size);
    it += instruction_size;
  }
}


// Returns set of instructions to invoke a method with no arguments. The
// method is assumed to be defined in the specified item of a constants tuple.
static std::vector<PythonInstruction> BuildMethodCall(int const_index) {
  std::vector<PythonInstruction> instructions;
  instructions.push_back(PythonInstructionArg(LOAD_CONST, const_index));
  instructions.push_back(PythonInstructionArg(CALL_FUNCTION, 0));
  instructions.push_back(PythonInstructionNoArg(POP_TOP));

  return instructions;
}


BytecodeManipulator::BytecodeManipulator(
    std::vector<uint8> bytecode,
    const bool has_lnotab,
    std::vector<uint8> lnotab)
    : has_lnotab_(has_lnotab) {
  data_.bytecode = std::move(bytecode);
  data_.lnotab = std::move(lnotab);

  strategy_ = STRATEGY_INSERT;  // Default strategy.
  for (auto it = data_.bytecode.begin(); it < data_.bytecode.end(); ) {
    PythonInstruction instruction = ReadInstruction(data_.bytecode, it);
    if (instruction.opcode == kInvalidInstruction.opcode) {
      strategy_ = STRATEGY_FAIL;
      break;
    }

    if (GetOpcodeType(instruction.opcode) == YIELD_OPCODE) {
      strategy_ = STRATEGY_APPEND;
      break;
    }

    it += instruction.size;
  }
}


bool BytecodeManipulator::InjectMethodCall(
    int offset,
    int callable_const_index) {
  Data new_data = data_;
  switch (strategy_) {
    case STRATEGY_INSERT:
      if (!InsertMethodCall(&new_data, offset, callable_const_index)) {
        return false;
      }
      break;

    case STRATEGY_APPEND:
      if (!AppendMethodCall(&new_data, offset, callable_const_index)) {
        return false;
      }
      break;

    default:
      return false;
  }

  data_ = std::move(new_data);
  return true;
}


// Use different algorithms to insert method calls for Python 2 and 3.
// Technically the algorithm for Python 3 will work with Python 2, but because
// it is more complicated and the issue of needing to upgrade branch
// instructions to use EXTENDED_ARG is less common, we stick with the existing
// algorithm for better safety.


#if PY_MAJOR_VERSION >= 3


// Represents a branch instruction in the original bytecode that may need to
// have its offsets fixed and/or upgraded to use EXTENDED_ARG.
struct UpdatedInstruction {
  PythonInstruction instruction;
  int original_size;
  int current_offset;
};


// Represents space that needs to be reserved for an insertion operation.
struct Insertion {
  int size;
  int current_offset;
};

// Max number of outer loop iterations to do before failing in
// InsertAndUpdateBranchInstructions.
static const int kMaxInsertionIterations = 10;


// Updates the line number table for an insertion in the bytecode.
// This is different than what the Python 2 version of InsertMethodCall() does.
// It should be more accurate, but is confined to Python 3 only for safety.
// This handles the case of adding insertion for EXTENDED_ARG better.
// Example for inserting 2 bytes at offset 2:
// lnotab: [{2, 1}, {4, 1}] // {offset_delta, line_delta}
// Old algorithm: [{2, 0}, {2, 1}, {4, 1}]
// New algorithm: [{2, 1}, {6, 1}]
// In the old version, trying to get the offset to insert a breakpoint right
// before line 1 would result in an offset of 2, which is inaccurate as the
// instruction before is an EXTENDED_ARG which will now be applied to the first
// instruction inserted instead of its original target.
static void InsertAndUpdateLnotab(int offset, int size,
                                  std::vector<uint8>* lnotab) {
  int current_offset = 0;
  for (auto it = lnotab->begin(); it != lnotab->end(); it += 2) {
    current_offset += it[0];

    if (current_offset > offset) {
      int remaining_size = it[0] + size;
      int remaining_lines = it[1];
      it = lnotab->erase(it, it + 2);
      while (remaining_size > 0xFF) {
        it = lnotab->insert(it, 0xFF) + 1;
        it = lnotab->insert(it, 0) + 1;
        remaining_size -= 0xFF;
      }
      it = lnotab->insert(it, remaining_size) + 1;
      it = lnotab->insert(it, remaining_lines) + 1;
      return;
    }
  }
}


// Reserves space for instructions to be inserted into the bytecode, and
// calculates the new offsets and arguments of branch instructions.
// Returns true if the calculation was successful, and false if too many
// iterations were needed.
//
// When inserting some space for the method call bytecode, branch instructions
// may need to have their offsets updated. Some cases might require branch
// instructions to be 'upgraded' to use EXTENDED_ARG if the new argument crosses
// the argument value limit for its current size.. This in turn will require
// another insertion and possibly further updates.
//
// It won't be manageable to update the bytecode in place in such cases, as when
// performing an insertion we might need to perform more insertions and quickly
// lose our place.
//
// Instead, we perform process insertion operations one at a time, starting from
// the original argument. While processing an operation, if an instruction needs
// to be upgraded to use EXTENDED_ARG, then another insertion operation is
// pushed on the stack to be processed later.
//
// Example:
// Suppose we need to reserve space for 6 bytes at offset 40. We have a
// JUMP_ABSOLUTE 250 instruction at offset 0, and a JUMP_FORWARD 2 instruction
// at offset 40.
// insertions: [{6, 40}]
// instructions: [{JUMP_ABSOLUTE 250, 0}, {JUMP_FORWARD 2, 40}]
//
// The JUMP_ABSOLUTE argument needs to be moved forward to 256, since the
// insertion occurs before the target. This requires an EXTENDED_ARG, so another
// insertion operation with size=2 at offset=0 is pushed.
// The JUMP_FORWARD instruction will be after the space reserved, so we need to
// update its current offset to now be 46. The argument does not need to be
// changed, as the insertion is not between its offset and target.
// insertions: [{2, 0}]
// instructions: [{JUMP_ABSOLUTE 256, 0}, {JUMP_FORWARD 2, 46}]
//
// For the next insertion, The JUMP_ABSOLUTE instruction's offset does not
// change, since it has the same offset as the insertion, signaling that the
// insertion is for the instruction itself. The argument gets updated to 258 to
// account for the additional space. The JUMP_FORWARD instruction's offset needs
// to be updated, but not its argument, for the same reason as before.
// insertions: []
// instructions: [{JUMP_ABSOLUTE 258, 0}, {JUMP_FORWARD 2, 48}]
//
// There are no more insertions so we are done.
static bool InsertAndUpdateBranchInstructions(
    Insertion insertion, std::vector<UpdatedInstruction>& instructions) {
  std::vector<Insertion> insertions { insertion };

  int iterations = 0;
  while (insertions.size() && iterations < kMaxInsertionIterations) {
    insertion = insertions.back();
    insertions.pop_back();

    // Update the offsets of all insertions after.
    for (auto it = insertions.begin(); it < insertions.end(); it++) {
      if (it->current_offset >= insertion.current_offset) {
        it->current_offset += insertion.size;
      }
    }

    // Update the offsets and arguments of the branches.
    for (auto it = instructions.begin();
         it < instructions.end(); it++) {
      PythonInstruction instruction = it->instruction;
      int32 arg = static_cast<int32>(instruction.argument);
      bool need_to_update = false;
      PythonOpcodeType opcode_type = GetOpcodeType(instruction.opcode);
      if (opcode_type == BRANCH_DELTA_OPCODE) {
        // For relative branches, the argument needs to be updated if the
        // insertion is between the instruction and the target.
        // The Python compiler sometimes prematurely adds EXTENDED_ARG with an
        // argument of 0 even when it is not required. This needs to be taken
        // into account when calculating the target of a branch instruction.
        int inst_size = std::max(instruction.size, it->original_size);
        int32 target = it->current_offset + inst_size + arg;
        need_to_update = it->current_offset < insertion.current_offset &&
                         insertion.current_offset < target;
      } else if (opcode_type == BRANCH_ABSOLUTE_OPCODE) {
        // For absolute branches, the argument needs to be updated if the
        // insertion before the target.
        need_to_update = insertion.current_offset < arg;
      }

      // If we are inserting the original method call instructions, we want to
      // update the current_offset of any instructions at or after. If we are
      // doing an EXTENDED_ARG insertion, we don't want to update the offset of
      // instructions right at the offset, because that is the original
      // instruction that the EXTENDED_ARG is for.
      int offset_diff = it->current_offset - insertion.current_offset;
      if ((iterations == 0 && offset_diff >= 0) || (offset_diff > 0)) {
        it->current_offset += insertion.size;
      }

      if (need_to_update) {
        PythonInstruction new_instruction =
            PythonInstructionArg(instruction.opcode, arg + insertion.size);
        int size_diff = new_instruction.size - instruction.size;
        if (size_diff > 0) {
          insertions.push_back(Insertion { size_diff, it->current_offset });
        }
        it->instruction = new_instruction;
      }
    }
    iterations++;
  }

  return insertions.size() == 0;
}


bool BytecodeManipulator::InsertMethodCall(
    BytecodeManipulator::Data* data,
    int offset,
    int const_index) const {
  std::vector<UpdatedInstruction> updated_instructions;
  bool offset_valid = false;

  // Gather all branch instructions.
  for (auto it = data->bytecode.begin(); it < data->bytecode.end();) {
    int current_offset = it - data->bytecode.begin();
    if (current_offset == offset) {
      DCHECK(!offset_valid) << "Each offset should be visited only once";
      offset_valid = true;
    }

    PythonInstruction instruction = ReadInstruction(data->bytecode, it);
    if (instruction.opcode == kInvalidInstruction.opcode) {
      return false;
    }

    PythonOpcodeType opcode_type = GetOpcodeType(instruction.opcode);
    if (opcode_type == BRANCH_DELTA_OPCODE ||
        opcode_type == BRANCH_ABSOLUTE_OPCODE) {
      updated_instructions.push_back(
          UpdatedInstruction { instruction, instruction.size, current_offset });
    }

    it += instruction.size;
  }

  if (!offset_valid) {
    LOG(ERROR) << "Offset " << offset << " is mid instruction or out of range";
    return false;
  }

  // Calculate new branch instructions.
  const std::vector<PythonInstruction> method_call_instructions =
      BuildMethodCall(const_index);
  int method_call_size = GetInstructionsSize(method_call_instructions);
  if (!InsertAndUpdateBranchInstructions({ method_call_size, offset },
                                         updated_instructions)) {
    LOG(ERROR) << "Too many instruction argument upgrades required";
    return false;
  }

  // Insert the method call.
  data->bytecode.insert(data->bytecode.begin() + offset, method_call_size, NOP);
  WriteInstructions(data->bytecode.begin() + offset, method_call_instructions);
  if (has_lnotab_) {
    InsertAndUpdateLnotab(offset, method_call_size, &data->lnotab);
  }

  // Write new branch instructions.
  // We can use current_offset directly since all insertions before would have
  // been done by the time we reach the current instruction.
  for (auto it = updated_instructions.begin();
       it < updated_instructions.end(); it++) {
    int size_diff = it->instruction.size - it->original_size;
    int offset = it->current_offset;
    if (size_diff > 0) {
      data->bytecode.insert(data->bytecode.begin() + offset, size_diff, NOP);
      if (has_lnotab_) {
        InsertAndUpdateLnotab(it->current_offset, size_diff, &data->lnotab);
      }
    } else if (size_diff < 0) {
      // The Python compiler sometimes prematurely adds EXTENDED_ARG with an
      // argument of 0 even when it is not required. Just leave it there, but
      // start writing the instruction after them.
      offset -= size_diff;
    }
    WriteInstruction(data->bytecode.begin() + offset, it->instruction);
  }

  return true;
}


#else


bool BytecodeManipulator::InsertMethodCall(
    BytecodeManipulator::Data* data,
    int offset,
    int const_index) const {
  const std::vector<PythonInstruction> method_call_instructions =
      BuildMethodCall(const_index);
  int size = GetInstructionsSize(method_call_instructions);

  bool offset_valid = false;
  for (auto it = data->bytecode.begin(); it < data->bytecode.end(); ) {
    const int current_offset = it - data->bytecode.begin();
    if (current_offset == offset) {
      DCHECK(!offset_valid) << "Each offset should be visited only once";
      offset_valid = true;
    }

    int current_fixed_offset = current_offset;
    if (current_fixed_offset >= offset) {
      current_fixed_offset += size;
    }

    PythonInstruction instruction = ReadInstruction(data->bytecode, it);
    if (instruction.opcode == kInvalidInstruction.opcode) {
      return false;
    }

    // Fix targets in branch instructions.
    switch (GetOpcodeType(instruction.opcode)) {
      case BRANCH_DELTA_OPCODE: {
        int32 delta = static_cast<int32>(instruction.argument);
        int32 target = current_offset + instruction.size + delta;

        if (target > offset) {
          target += size;
        }

        int32 fixed_delta = target - current_fixed_offset - instruction.size;
        if (delta != fixed_delta) {
          PythonInstruction new_instruction =
              PythonInstructionArg(instruction.opcode, fixed_delta);
          if (new_instruction.size != instruction.size) {
            LOG(ERROR) << "Upgrading instruction to extended not supported";
            return false;
          }

          WriteInstruction(it, new_instruction);
        }
        break;
      }

      case BRANCH_ABSOLUTE_OPCODE:
        if (static_cast<int32>(instruction.argument) > offset) {
          PythonInstruction new_instruction = PythonInstructionArg(
              instruction.opcode, instruction.argument + size);
          if (new_instruction.size != instruction.size) {
            LOG(ERROR) << "Upgrading instruction to extended not supported";
            return false;
          }

          WriteInstruction(it, new_instruction);
        }
        break;

      default:
        break;
    }

    it += instruction.size;
  }

  if (!offset_valid) {
    LOG(ERROR) << "Offset " << offset << " is mid instruction or out of range";
    return false;
  }

  // Insert the bytecode to invoke the callable.
  data->bytecode.insert(data->bytecode.begin() + offset, size, NOP);
  WriteInstructions(data->bytecode.begin() + offset, method_call_instructions);

  // Insert a new entry into line table to account for the new bytecode.
  if (has_lnotab_) {
    int current_offset = 0;
    for (auto it = data->lnotab.begin(); it != data->lnotab.end(); it += 2) {
      current_offset += it[0];

      if (current_offset >= offset) {
        int remaining_size = size;
        while (remaining_size > 0) {
          const int current_size = std::min(remaining_size, 0xFF);
          it = data->lnotab.insert(it, static_cast<uint8>(current_size)) + 1;
          it = data->lnotab.insert(it, 0) + 1;
          remaining_size -= current_size;
        }

        break;
      }
    }
  }

  return true;
}
#endif


// This method does not change line numbers table. The line numbers table
// is monotonically growing, which is not going to work for our case. Besides
// the trampoline will virtually always fit a single instruction, so we don't
// really need to update line numbers table.
bool BytecodeManipulator::AppendMethodCall(
    BytecodeManipulator::Data* data,
    int offset,
    int const_index) const {
  PythonInstruction trampoline =
      PythonInstructionArg(JUMP_ABSOLUTE, data->bytecode.size());

  std::vector<PythonInstruction> relocated_instructions;
  int relocated_size = 0;
  for (auto it = data->bytecode.begin() + offset;
      relocated_size < trampoline.size; ) {
    if (it >= data->bytecode.end()) {
      LOG(ERROR) << "Not enough instructions";
      return false;
    }

    PythonInstruction instruction = ReadInstruction(data->bytecode, it);
    if (instruction.opcode == kInvalidInstruction.opcode) {
      return false;
    }

    const PythonOpcodeType opcode_type = GetOpcodeType(instruction.opcode);

    // We are writing "jump" instruction to the breakpoint location. All
    // instructions that get rewritten are relocated to the new breakpoint
    // block. Unfortunately not all instructions can be moved:
    // 1. Instructions with relative offset can't be moved forward, because
    //    the offset can't be negative.
    //    TODO: FORWARD_JUMP can be replaced with ABSOLUTE_JUMP.
    // 2. YIELD_VALUE can't be moved because generator object keeps the frame
    //    object in between "yield" calls. If the breakpoint is added or
    //    removed, subsequent calls into the generator will jump into invalid
    //    location.
    if ((opcode_type == BRANCH_DELTA_OPCODE) ||
        (opcode_type == YIELD_OPCODE)) {
      LOG(ERROR) << "Not enough space for trampoline";
      return false;
    }

    relocated_instructions.push_back(instruction);
    relocated_size += instruction.size;
    it += instruction.size;
  }

  for (auto it = data->bytecode.begin(); it < data->bytecode.end(); ) {
    PythonInstruction instruction = ReadInstruction(data->bytecode, it);
    if (instruction.opcode == kInvalidInstruction.opcode) {
      return false;
    }

    const PythonOpcodeType opcode_type = GetOpcodeType(instruction.opcode);
    if ((opcode_type == BRANCH_DELTA_OPCODE) ||
        (opcode_type == BRANCH_ABSOLUTE_OPCODE)) {
      const int branch_target =
          GetBranchTarget(it - data->bytecode.begin(), instruction);

      // Consider this bytecode:
      //       0  LOAD_CONST 6
      //       1  NOP
      //       2  LOAD_CONST 7
      //       5  ...
      //       ...
      // Suppose we insert breakpoint into offset 1. The new bytecode will be:
      //       0  LOAD_CONST 6
      //       1  JUMP_ABSOLUTE 100
      //       4  NOP
      //       5  ...
      //       ...
      //     100  NOP                # First relocated instruction.
      //     101  LOAD_CONST 7       # Second relocated instruction.
      //     ...
      //          JUMP_ABSOLUTE 5    # Go back to the normal code flow.
      // It is perfectly fine to have a jump (either relative or absolute) into
      // offset 1. It will jump to offset 100 and run the relocated
      // instructions. However it is not OK to jump into offset 2. It was
      // instruction boundary in the original code, but it's mid-instruction
      // in the new code. Some instructions could be theoretically updated
      // (like JUMP_ABSOLUTE can be updated). We don't bother with it since
      // this issue is not common enough.
      if ((branch_target > offset) &&
          (branch_target < offset + relocated_size)) {
        LOG(ERROR) << "Jump into relocated instruction detected";
        return false;
      }
    }

    it += instruction.size;
  }

  std::vector<PythonInstruction> appendix = BuildMethodCall(const_index);
  appendix.insert(
      appendix.end(),
      relocated_instructions.begin(),
      relocated_instructions.end());
  appendix.push_back(PythonInstructionArg(
      JUMP_ABSOLUTE,
      offset + relocated_size));

  // Write the appendix instructions.
  int pos = data->bytecode.size();
  data->bytecode.resize(pos + GetInstructionsSize(appendix));
  WriteInstructions(data->bytecode.begin() + pos, appendix);

  // Insert jump to trampoline.
  WriteInstruction(data->bytecode.begin() + offset, trampoline);
  std::fill(
      data->bytecode.begin() + offset + trampoline.size,
      data->bytecode.begin() + offset + relocated_size,
      NOP);

  return true;
}

}  // namespace cdbg
}  // namespace devtools
