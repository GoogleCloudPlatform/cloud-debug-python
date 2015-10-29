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

// Single Python instruction. There are 3 types of instructions:
// 1. Instruction without arguments (takes 1 byte).
// 2. Instruction with a single 16 bit argument (takes 3 bytes).
// 3. Instruction with a 32 bit argument (very uncommon; takes 6 bytes).
struct PythonInstruction {
  uint8 opcode;
  uint32 argument;
  bool is_extended;
};

// Special pseudo-instruction to indicate failures.
static const PythonInstruction kInvalidInstruction { 0xFF, 0xFFFFFFFF, false };

// Creates an instance of PythonInstruction for instruction with no arguments.
static PythonInstruction PythonInstructionNoArg(uint8 opcode) {
  DCHECK(!HAS_ARG(opcode));

  PythonInstruction instruction;
  instruction.opcode = opcode;
  instruction.argument = 0;
  instruction.is_extended = false;

  return instruction;
}


// Creates an instance of PythonInstruction for instruction with an argument.
static PythonInstruction PythonInstructionArg(uint8 opcode, uint32 argument) {
  DCHECK(HAS_ARG(opcode));

  PythonInstruction instruction;
  instruction.opcode = opcode;
  instruction.argument = argument;
  instruction.is_extended = (argument > 0xFFFF);

  return instruction;
}


// Calculates the number of bytes that an instruction occupies.
static int GetInstructionSize(const PythonInstruction& instruction) {
  if (instruction.is_extended) {
    return 6;  // Extended instruction with a 32 bit argument.
  }

  if (HAS_ARG(instruction.opcode)) {
    return 3;  // Instruction with a single 16 bit argument.
  }

  return 1;    // Instruction without argument.
}


// Calculates the size of a set of instructions.
static int GetInstructionsSize(
    const std::vector<PythonInstruction>& instructions) {
  int size = 0;
  for (auto it = instructions.begin(); it != instructions.end(); ++it) {
    size += GetInstructionSize(*it);
  }

  return size;
}


// Classification of an opcode.
static PythonOpcodeType GetOpcodeType(uint8 opcode) {
  switch (opcode) {
    case YIELD_VALUE:
      return YIELD_OPCODE;

    case FOR_ITER:
    case JUMP_FORWARD:
    case SETUP_LOOP:
    case SETUP_EXCEPT:
    case SETUP_FINALLY:
    case SETUP_WITH:
      return BRANCH_DELTA_OPCODE;

    case JUMP_IF_FALSE_OR_POP:
    case JUMP_IF_TRUE_OR_POP:
    case JUMP_ABSOLUTE:
    case POP_JUMP_IF_FALSE:
    case POP_JUMP_IF_TRUE:
    case CONTINUE_LOOP:
      return BRANCH_ABSOLUTE_OPCODE;

    default:
      return SEQUENTIAL_OPCODE;
  }
}


// Gets the target offset of a branch instruction.
static int GetBranchTarget(int offset, PythonInstruction instruction) {
  const int argument_value = instruction.is_extended
      ? static_cast<int32>(instruction.argument)
      : static_cast<int16>(instruction.argument);

  switch (GetOpcodeType(instruction.opcode)) {
    case BRANCH_DELTA_OPCODE:
      return offset + GetInstructionSize(instruction) + argument_value;

    case BRANCH_ABSOLUTE_OPCODE:
      return argument_value;

    default:
      DCHECK(false) << "Not a branch instruction";
      return -1;
  }
}


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


// Read instruction at the specified offset. Returns kInvalidInstruction
// buffer underflow.
static PythonInstruction ReadInstruction(
    const std::vector<uint8>& bytecode,
    std::vector<uint8>::const_iterator it) {
  PythonInstruction instruction { 0, 0, false };

  if (it == bytecode.end()) {
    LOG(ERROR) << "Buffer underflow";
    return kInvalidInstruction;
  }

  instruction.opcode = it[0];

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
    instruction.is_extended = true;
  } else if (HAS_ARG(instruction.opcode)) {
    if (bytecode.end() - it < 3) {
      LOG(ERROR) << "Buffer underflow";
      return kInvalidInstruction;
    }

    instruction.argument = ReadPythonBytecodeUInt16(it_arg);
  }

  return instruction;
}


// Writes instruction to the specified destination. The caller is responsible
// to make sure the target vector has enough space. Returns size of an
// instruction.
static int WriteInstruction(
    std::vector<uint8>::iterator it,
    const PythonInstruction& instruction) {
  if (instruction.is_extended) {
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
}


// Write set of instructions to the specified destination.
static void WriteInstructions(
    std::vector<uint8>::iterator it,
    const std::vector<PythonInstruction>& instructions) {
  for (auto it_instruction = instructions.begin();
       it_instruction != instructions.end();
       ++it_instruction) {
    const int instruction_size = WriteInstruction(it, *it_instruction);
    DCHECK_EQ(instruction_size, GetInstructionSize(*it_instruction));
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

    if (instruction.opcode == YIELD_VALUE) {
      strategy_ = STRATEGY_APPEND;
      break;
    }

    it += GetInstructionSize(instruction);
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

    const int instruction_size = GetInstructionSize(instruction);

    // Fix targets in branch instructions.
    switch (instruction.opcode) {
      // Delta target argument.
      case FOR_ITER:
      case JUMP_FORWARD:
      case SETUP_LOOP:
      case SETUP_EXCEPT:
      case SETUP_FINALLY:
      case SETUP_WITH: {
        int32 delta = instruction.is_extended
            ? static_cast<int32>(instruction.argument)
            : static_cast<int16>(instruction.argument);

        int32 target = current_offset + instruction_size + delta;
        if (target > offset) {
          target += size;
        }

        int32 fixed_delta = target - current_fixed_offset - instruction_size;
        if (delta != fixed_delta) {
          if (instruction.is_extended) {
            instruction.argument = static_cast<uint32>(fixed_delta);
          } else {
            if (static_cast<int16>(delta) != delta) {
              LOG(ERROR) << "Upgrading instruction to extended not supported";
              return false;
            }

            instruction.argument = static_cast<uint16>(fixed_delta);
          }

          WriteInstruction(it, instruction);
        }

        break;
      }

      // Absolute target argument.
      case JUMP_IF_FALSE_OR_POP:
      case JUMP_IF_TRUE_OR_POP:
      case JUMP_ABSOLUTE:
      case POP_JUMP_IF_FALSE:
      case POP_JUMP_IF_TRUE:
      case CONTINUE_LOOP:
        if (static_cast<int>(instruction.argument) > offset) {
          instruction.argument += size;
          if (!instruction.is_extended && (instruction.argument > 0xFFFF)) {
            LOG(ERROR) << "Upgrading instruction to extended not supported";
            return false;
          }

          WriteInstruction(it, instruction);
        }
        break;
    }

    it += instruction_size;
  }

  if (!offset_valid) {
    LOG(ERROR) << "Offset " << offset << " is mid instruction or out of range";
    return false;
  }

  // Insert the bytecode to invoke the callable.
  data->bytecode.insert(data->bytecode.begin() + offset, size, STOP_CODE);
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


// This method does not change line numbers table. The line numbers table
// is monotonically growing, which is not going to work for our case. Besides
// the trampoline will virtually always fit a single instruction, so we don't
// really need to update line numbers table.
bool BytecodeManipulator::AppendMethodCall(
    BytecodeManipulator::Data* data,
    int offset,
    int const_index) const {
  PythonInstruction trampoline;
  trampoline.opcode = JUMP_ABSOLUTE;
  trampoline.is_extended = false;
  trampoline.argument = data->bytecode.size();

  const int trampoline_size = GetInstructionSize(trampoline);

  std::vector<PythonInstruction> relocated_instructions;
  int relocated_size = 0;
  for (auto it = data->bytecode.begin() + offset;
      relocated_size < trampoline_size; ) {
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
    //    TODO(vlif): FORWARD_JUMP can be replaced with ABSOLUTE_JUMP.
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
    relocated_size += GetInstructionSize(instruction);
    it += GetInstructionSize(instruction);
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
      //       4  STOP_CODE
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

    it += GetInstructionSize(instruction);
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
      data->bytecode.begin() + offset + trampoline_size,
      data->bytecode.begin() + offset + relocated_size,
      STOP_CODE);

  return true;
}

}  // namespace cdbg
}  // namespace devtools


