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

#include "bytecode_breakpoint.h"

#include "python_callback.h"
#include "python_util.h"

namespace devtools {
namespace cdbg {

// Each method in python has a tuple with all the constants instructions use.
// Breakpoint patching appends more constants. If the index of new constant
// exceed 0xFFFF, breakpoint patching would need to use extended instructions,
// which is not supported. We therefore limit to methods with up to 0xF000
// instructions that leaves us with up to 0x0FFF breakpoints.
static const int kMaxCodeObjectConsts = 0xF000;

struct MethodCallBytecode {
  explicit MethodCallBytecode(uint16 callable_const_index)
      : opcode1(LOAD_CONST),
        opcode1_arg(callable_const_index),
        opcode2(CALL_FUNCTION),
        opcode2_arg(0),
        opcode3(POP_TOP) {
  }

  const uint8 opcode1;
  const uint16 opcode1_arg;
  const uint8 opcode2;
  const uint16 opcode2_arg;
  const uint8 opcode3;
} __attribute__((packed,aligned(1)));

static_assert(sizeof(MethodCallBytecode) == 7, "packed");

struct PythonInstruction {
  uint8 opcode;
  uint32 argument;
  bool is_extended;
};


static const PythonInstruction kInvalidInstruction { 0xFF, 0xFFFFFFFF, false };


// Creates a new tuple by appending "items" to elements in "tuple".
static ScopedPyObject AppendTuple(
    PyObject* tuple,
    const std::vector<PyObject*>& items) {
  const size_t tuple_size = PyTuple_GET_SIZE(tuple);
  ScopedPyObject new_tuple(PyTuple_New(tuple_size + items.size()));

  for (size_t i = 0; i < tuple_size; ++i) {
    PyObject* item = PyTuple_GET_ITEM(tuple, i);
    Py_XINCREF(item);
    PyTuple_SET_ITEM(new_tuple.get(), i, item);
  }

  for (size_t i = 0; i < items.size(); ++i) {
    Py_XINCREF(items[i]);
    PyTuple_SET_ITEM(new_tuple.get(), tuple_size + i, items[i]);
  }

  return new_tuple;
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


// Calculates the number of bytes that an instruction occupies.
int GetInstructionSize(const PythonInstruction& instruction) {
  if (instruction.is_extended) {
    return 6;  // Extended instruction with a 32 bit argument.
  }

  if (HAS_ARG(instruction.opcode)) {
    return 3;  // Instruction with a single 16 bit argument.
  }

  return 1;    // Instruction without argument.
}


// Read instruction at the specified offset. Returns kInvalidInstruction
// buffer underflow.
PythonInstruction ReadInstruction(
    const std::vector<uint8>& bytecode,
    std::vector<uint8>::const_iterator it) {
  PythonInstruction instruction { 0, 0, false };

  if (it == bytecode.end()) {
    LOG(ERROR) << "Buffer underflow";
    return kInvalidInstruction;
  }

  instruction.opcode = it[0];

  std::vector<uint8>::const_iterator it_arg = it + 1;
  if (instruction.opcode == EXTENDED_ARG) {
    if (bytecode.end() - it < 6) {
      LOG(ERROR) << "Buffer underflow";
      return kInvalidInstruction;
    }

    instruction.opcode = it[3];

    std::vector<uint8>::const_iterator it_ext = it + 4;
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
// to make sure the target vector has enough space.
void WriteInstruction(
    std::vector<uint8>::iterator it,
    const PythonInstruction& instruction) {
  if (instruction.is_extended) {
    it[0] = EXTENDED_ARG;
    WritePythonBytecodeUInt16(it + 1, instruction.argument >> 16);
    it[3] = instruction.opcode;
    WritePythonBytecodeUInt16(
        it + 4,
        static_cast<uint16>(instruction.argument));
  } else {
    it[0] = instruction.opcode;

    if (HAS_ARG(instruction.opcode)) {
      DCHECK_LE(instruction.argument, 0xFFFFU);
      WritePythonBytecodeUInt16(
          it + 1,
          static_cast<uint16>(instruction.argument));
    }
  }
}


// Rewrites the method bytecode to invoke callable at the specified offset.
// Returns false on errors (e.g. invalid offset). If this function fails,
// the "bytecode" might be messed up.
static bool InsertMethodCall(
    std::vector<uint8>* bytecode,
    bool has_lnotab,
    std::vector<uint8>* lnotab,
    int offset,
    int callable_const_index) {
  MethodCallBytecode method_call_bytecode(callable_const_index);

  bool offset_valid = false;
  for (auto it = bytecode->begin(); it < bytecode->end(); ) {
    const int current_offset = it - bytecode->begin();
    if (current_offset == offset) {
      DCHECK(!offset_valid) << "Each offset should be visited only once";
      offset_valid = true;
    }

    int current_fixed_offset = current_offset;
    if (current_fixed_offset >= offset) {
      current_fixed_offset += sizeof(method_call_bytecode);
    }

    PythonInstruction instruction = ReadInstruction(*bytecode, it);
    if (instruction.opcode == kInvalidInstruction.opcode) {
      return false;
    }

    const int instruction_size = GetInstructionSize(instruction);

    // Fix targets in branch instructions.
    bool update_instruction = false;
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
          target += sizeof(method_call_bytecode);
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

          update_instruction = true;
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
          instruction.argument += sizeof(method_call_bytecode);
          if (!instruction.is_extended && (instruction.argument > 0xFFFF)) {
            LOG(ERROR) << "Upgrading instruction to extended not supported";
            return false;
          }

          update_instruction = true;
        }
        break;
    }

    if (update_instruction) {
      WriteInstruction(it, instruction);
    }

    it += instruction_size;
  }

  if (!offset_valid) {
    LOG(ERROR) << "Offset " << offset << " is mid instruction";
    return false;
  }

  // Insert the bytecode to invoke the callable.
  bytecode->insert(
      bytecode->begin() + offset,
      reinterpret_cast<uint8*>(&method_call_bytecode),
      reinterpret_cast<uint8*>(&method_call_bytecode + 1));

  // Insert a new entry into line table to account for the new bytecode.
  if (has_lnotab) {
    int current_offset = 0;
    bool inserted = false;
    for (auto it = lnotab->begin(); ; it += 2) {
      if (current_offset == offset) {
        const uint8 row[] = { sizeof(method_call_bytecode), 0 };
        lnotab->insert(it, row, row + arraysize(row));

        inserted = true;
        break;
      }

      if (it + 1 < lnotab->end()) {
        current_offset += *it;
        continue;
      }

      break;
    }

    if (!inserted) {
      LOG(ERROR) << "Failed to update line table";
      return false;
    }
  }

  return true;
}


BytecodeBreakpoint::BytecodeBreakpoint()
    : cookie_counter_(1000000) {
}


BytecodeBreakpoint::~BytecodeBreakpoint() {
  Detach();
}


void BytecodeBreakpoint::Detach() {
  for (auto it = patches_.begin(); it != patches_.end(); ++it) {
    it->second->breakpoints.clear();
    PatchCodeObject(it->second);

    // TODO(vlif): assert zombie_refs.empty() after garbage collection
    // for zombie refs is implemented.

    delete it->second;
  }

  patches_.clear();

  for (auto it = cookie_map_.begin(); it != cookie_map_.end(); ++it) {
    delete it->second;
  }

  cookie_map_.clear();
}


int BytecodeBreakpoint::SetBreakpoint(
    PyCodeObject* code_object,
    int line,
    std::function<void()> hit_callback,
    std::function<void()> error_callback) {
  CodeObjectBreakpoints* code_object_breakpoints =
      PreparePatchCodeObject(ScopedPyCodeObject::NewReference(code_object));
  if (code_object_breakpoints == nullptr) {
    error_callback();
    return -1;  // Not a valid cookie, but "ClearBreakpoint" wouldn't mind.
  }

  // Find the offset of the instruction at "line". We use original line
  // table in case "code_object" is already patched with another breakpoint.
  CodeObjectLinesEnumerator lines_enumerator(
      code_object->co_firstlineno,
      code_object_breakpoints->original_lnotab.get());
  while (lines_enumerator.line_number() != line) {
    if (!lines_enumerator.Next()) {
      LOG(ERROR) << "Line " << line << " not found in "
                 << CodeObjectDebugString(code_object);
      error_callback();
      return -1;
    }
  }

  // Assign cookie to this breakpoint and Register it.
  const int cookie = cookie_counter_++;

  std::unique_ptr<Breakpoint> breakpoint(new Breakpoint);
  breakpoint->code_object = ScopedPyCodeObject::NewReference(code_object);
  breakpoint->offset = lines_enumerator.offset();
  breakpoint->hit_callable = PythonCallback::Wrap(hit_callback);
  breakpoint->error_callback = error_callback;
  breakpoint->cookie = cookie;

  code_object_breakpoints->breakpoints.insert(
      std::make_pair(breakpoint->offset, breakpoint.get()));

  DCHECK(cookie_map_[cookie] == nullptr);
  cookie_map_[cookie] = breakpoint.release();

  PatchCodeObject(code_object_breakpoints);

  return cookie;
}


void BytecodeBreakpoint::ClearBreakpoint(int cookie) {
  auto it_breakpoint = cookie_map_.find(cookie);
  if (it_breakpoint == cookie_map_.end()) {
    return;  // No breakpoint with this cookie.
  }

  auto it_code = patches_.find(it_breakpoint->second->code_object);
  if (it_code != patches_.end()) {
    CodeObjectBreakpoints* code = it_code->second;

    auto it = code->breakpoints.begin();
    int erase_count = 0;
    while (it != code->breakpoints.end()) {
      if (it->second == it_breakpoint->second) {
        code->breakpoints.erase(it);
        ++erase_count;
        it = code->breakpoints.begin();
      } else {
        ++it;
      }
    }

    DCHECK_EQ(1, erase_count);

    PatchCodeObject(code);

    if (code->breakpoints.empty() && code->zombie_refs.empty()) {
      delete it_code->second;
      patches_.erase(it_code);
    }
  } else {
    DCHECK(false) << "Missing code object";
  }

  delete it_breakpoint->second;
  cookie_map_.erase(it_breakpoint);
}


BytecodeBreakpoint::CodeObjectBreakpoints*
BytecodeBreakpoint::PreparePatchCodeObject(
    const ScopedPyCodeObject& code_object) {
  if (code_object.is_null() || !PyCode_Check(code_object.get())) {
    LOG(ERROR) << "Bad code_object argument";
    return nullptr;
  }

  auto it = patches_.find(code_object);
  if (it != patches_.end()) {
    return it->second;  // Already loaded.
  }

  std::unique_ptr<CodeObjectBreakpoints> data(new CodeObjectBreakpoints);
  data->code_object = code_object;
  data->original_stacksize = code_object.get()->co_stacksize;

  data->original_consts =
      ScopedPyObject::NewReference(code_object.get()->co_consts);
  if ((data->original_consts == nullptr) ||
      !PyTuple_CheckExact(data->original_consts.get())) {
    LOG(ERROR) << "Code object has null or corrupted constants tuple";
    return nullptr;
  }

  if (PyTuple_GET_SIZE(data->original_consts.get()) >= kMaxCodeObjectConsts) {
    LOG(ERROR) << "Code objects with more than "
               << kMaxCodeObjectConsts << " constants not supported";
    return nullptr;
  }

  data->original_code =
      ScopedPyObject::NewReference(code_object.get()->co_code);
  if ((data->original_code == nullptr) ||
      !PyString_CheckExact(data->original_code.get())) {
    LOG(ERROR) << "Code object has no code";
    return nullptr;  // Probably a built-in method or uninitialized code object.
  }

  data->original_lnotab =
      ScopedPyObject::NewReference(code_object.get()->co_lnotab);

  patches_[code_object] = data.get();
  return data.release();
}


void BytecodeBreakpoint::PatchCodeObject(CodeObjectBreakpoints* code) {
  PyCodeObject* code_object = code->code_object.get();

  if (code->breakpoints.empty()) {
    VLOG(1) << "Restoring code object to original state: "
            << CodeObjectDebugString(code_object);

    code->zombie_refs.push_back(ScopedPyObject(code_object->co_consts));
    code_object->co_consts = code->original_consts.get();
    Py_INCREF(code_object->co_consts);

    code_object->co_stacksize = code->original_stacksize;

    code->zombie_refs.push_back(ScopedPyObject(code_object->co_code));
    code_object->co_code = code->original_code.get();
    Py_INCREF(code_object->co_code);

    if (code_object->co_lnotab != nullptr) {
      code->zombie_refs.push_back(ScopedPyObject(code_object->co_lnotab));
    }
    code_object->co_lnotab = code->original_lnotab.get();
    Py_INCREF(code_object->co_lnotab);

    return;
  }

  const size_t bytecode_size = PyString_GET_SIZE(code->original_code.get());
  const uint8* const bytecode_data = reinterpret_cast<uint8*>(
      PyString_AS_STRING(code->original_code.get()));
  std::vector<uint8> bytecode(bytecode_data, bytecode_data + bytecode_size);

  bool has_lnotab = false;
  std::vector<uint8> lnotab;
  if (!code->original_lnotab.is_null() &&
      PyString_CheckExact(code->original_lnotab.get())) {
    const size_t lnotab_size = PyString_GET_SIZE(code->original_lnotab.get());
    const uint8* const lnotab_data = reinterpret_cast<uint8*>(
        PyString_AS_STRING(code->original_lnotab.get()));

    has_lnotab = true;
    lnotab.assign(lnotab_data, lnotab_data + lnotab_size);
  }

  // Add callbacks to code object constants and patch the bytecode.
  std::vector<PyObject*> callbacks;
  callbacks.reserve(code->breakpoints.size());

  int const_index = PyTuple_GET_SIZE(code->original_consts.get());
  for (auto it_entry = code->breakpoints.begin();
       it_entry != code->breakpoints.end();
       ++it_entry, ++const_index) {
    const int offset = it_entry->first;
    const Breakpoint& breakpoint = *it_entry->second;
    DCHECK_EQ(offset, breakpoint.offset);

    callbacks.push_back(breakpoint.hit_callable.get());

    std::vector<uint8> new_bytecode = bytecode;
    std::vector<uint8> new_lnotab = lnotab;
    if (InsertMethodCall(&new_bytecode,
                         has_lnotab,
                         &new_lnotab,
                         offset,
                         const_index)) {
      std::swap(bytecode, new_bytecode);
      std::swap(lnotab, new_lnotab);
    } else {
      LOG(WARNING) << "Failed to insert bytecode for breakpoint "
                   << breakpoint.cookie;
      breakpoint.error_callback();
    }
  }

  // Create the constants tuple, the new bytecode string and line table.
  code->zombie_refs.push_back(ScopedPyObject(code_object->co_consts));
  ScopedPyObject consts = AppendTuple(code->original_consts.get(), callbacks);
  code_object->co_consts = consts.release();

  code_object->co_stacksize = code->original_stacksize + 1;

  code->zombie_refs.push_back(ScopedPyObject(code_object->co_code));
  ScopedPyObject bytecode_string(PyString_FromStringAndSize(
      reinterpret_cast<const char*>(bytecode.data()),
      bytecode.size()));
  DCHECK(!bytecode_string.is_null());
  code_object->co_code = bytecode_string.release();

  if (has_lnotab) {
    code->zombie_refs.push_back(ScopedPyObject(code_object->co_lnotab));
    ScopedPyObject lnotab_string(PyString_FromStringAndSize(
        reinterpret_cast<const char*>(lnotab.data()),
        lnotab.size()));
    DCHECK(!lnotab_string.is_null());
    code_object->co_lnotab = lnotab_string.release();
  }

  VLOG(1) << "Code object patched with " << code->breakpoints.size()
          << " breakpoints: " << CodeObjectDebugString(code_object);
}

}  // namespace cdbg
}  // namespace devtools


