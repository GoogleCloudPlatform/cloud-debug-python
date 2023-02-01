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
#include <cstdint>

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
// In Python 3.6, there are 4 types of instructions:
// 1. Instructions without arguments, or a 8 bit argument (takes 2 bytes).
// 2. Instructions with a 16 bit argument (takes 4 bytes).
// 3. Instructions with a 24 bit argument (takes 6 bytes).
// 4. Instructions with a 32 bit argument (takes 8 bytes).
//
// To handle 16-32 bit arguments in Python 3,
// a special instruction with an opcode of EXTENDED_ARG is prepended to the
// actual instruction. The argument of the EXTENDED_ARG instruction is combined
// with the argument of the next instruction to form the full argument.
struct PythonInstruction {
  uint8_t opcode;
  uint32_t argument;
  int size;
};

// Special pseudo-instruction to indicate failures.
static const PythonInstruction kInvalidInstruction { 0xFF, 0xFFFFFFFF,  0 };

// Creates an instance of PythonInstruction for instruction with no arguments.
static PythonInstruction PythonInstructionNoArg(uint8_t opcode) {
  DCHECK(!HAS_ARG(opcode));

  PythonInstruction instruction;
  instruction.opcode = opcode;
  instruction.argument = 0;

  instruction.size = 2;

  return instruction;
}

// Creates an instance of PythonInstruction for instruction with an argument.
static PythonInstruction PythonInstructionArg(uint8_t opcode,
                                              uint32_t argument) {
  DCHECK(HAS_ARG(opcode));

  PythonInstruction instruction;
  instruction.opcode = opcode;
  instruction.argument = argument;

  if (argument <= 0xFF) {
    instruction.size = 2;
  } else if (argument <= 0xFFFF) {
    instruction.size = 4;
  } else if (argument <= 0xFFFFFF) {
    instruction.size = 6;
  } else {
    instruction.size = 8;
  }

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
static PythonOpcodeType GetOpcodeType(uint8_t opcode) {
  switch (opcode) {
    case YIELD_VALUE:
    case YIELD_FROM:
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
#if PY_VERSION_HEX >= 0x03080000 && PY_VERSION_HEX < 0x03090000
    // Added in Python 3.8 and removed in 3.9
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


// Read instruction at the specified offset. Returns kInvalidInstruction
// buffer underflow.
static PythonInstruction ReadInstruction(
    const std::vector<uint8_t>& bytecode,
    std::vector<uint8_t>::const_iterator it) {
  PythonInstruction instruction { 0, 0, 0 };

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

  return instruction;
}

// Writes instruction to the specified destination. The caller is responsible
// to make sure the target vector has enough space. Returns size of an
// instruction.
static int WriteInstruction(std::vector<uint8_t>::iterator it,
                            const PythonInstruction& instruction) {
  uint32_t arg = instruction.argument;
  int size_written = 0;
  // Start writing backwards from the real instruction, followed by any
  // EXTENDED_ARG instructions if needed.
  for (int i = instruction.size - 2; i >= 0; i -= 2) {
    it[i] = size_written == 0 ? instruction.opcode : EXTENDED_ARG;
    it[i + 1] = static_cast<uint8_t>(arg);
    arg = arg >> 8;
    size_written += 2;
  }
  return size_written;
}

// Write set of instructions to the specified destination.
static void WriteInstructions(
    std::vector<uint8_t>::iterator it,
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

BytecodeManipulator::BytecodeManipulator(std::vector<uint8_t> bytecode,
                                         const bool has_linedata,
                                         std::vector<uint8_t> linedata)
    : has_linedata_(has_linedata) {
  data_.bytecode = std::move(bytecode);
  data_.linedata = std::move(linedata);

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

#if PY_VERSION_HEX < 0x030A0000
// Updates the line number table for an insertion in the bytecode.
// Example for inserting 2 bytes at offset 2:
// lnotab:  [{2, 1}, {4, 1}] // {offset_delta, line_delta}
// updated: [{2, 1}, {6, 1}]
static void InsertAndUpdateLineData(int offset, int size,
                                    std::vector<uint8_t>* lnotab) {
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
#else
// Updates the line number table for an insertion in the bytecode.
// Example for inserting 2 bytes at offset 2:
// linetable: [{2, 1}, {4, 1}] // {address_end_delta, line_delta}
// updated:   [{2, 1}, {6, 1}]
//
// For more information on the linetable format in Python 3.10, see:
// https://github.com/python/cpython/blob/main/Objects/lnotab_notes.txt
static void InsertAndUpdateLineData(int offset, int size,
                                    std::vector<uint8_t>* linetable) {
  int current_offset = 0;
  for (auto it = linetable->begin(); it != linetable->end(); it += 2) {
    current_offset += it[0];

    if (current_offset > offset) {
      int remaining_size = it[0] + size;
      int remaining_lines = it[1];
      it = linetable->erase(it, it + 2);
      while (remaining_size > 0xFE) {  // Max address delta is listed as 254.
        it = linetable->insert(it, 0xFE) + 1;
        it = linetable->insert(it, 0) + 1;
        remaining_size -= 0xFE;
      }
      it = linetable->insert(it, remaining_size) + 1;
      it = linetable->insert(it, remaining_lines) + 1;
      return;
    }
  }
}
#endif

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
      int32_t arg = static_cast<int32_t>(instruction.argument);
      bool need_to_update = false;
      PythonOpcodeType opcode_type = GetOpcodeType(instruction.opcode);
      if (opcode_type == BRANCH_DELTA_OPCODE) {
        // For relative branches, the argument needs to be updated if the
        // insertion is between the instruction and the target.
        // The Python compiler sometimes prematurely adds EXTENDED_ARG with an
        // argument of 0 even when it is not required. This needs to be taken
        // into account when calculating the target of a branch instruction.
        int inst_size = std::max(instruction.size, it->original_size);
        int32_t target = it->current_offset + inst_size + arg;
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
#if PY_VERSION_HEX < 0x030A0000
        int delta = insertion.size;
#else
        // Changed in version 3.10: The argument of jump, exception handling
        // and loop instructions is now the instruction offset rather than the
        // byte offset.
        int delta = insertion.size / 2;
#endif
        PythonInstruction new_instruction =
            PythonInstructionArg(instruction.opcode, arg + delta);
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
  if (has_linedata_) {
    InsertAndUpdateLineData(offset, method_call_size, &data->linedata);
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
      if (has_linedata_) {
        InsertAndUpdateLineData(it->current_offset, size_diff, &data->linedata);
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
