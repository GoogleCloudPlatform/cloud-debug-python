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

#include <cstdint>

#include "bytecode_manipulator.h"
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

    // TODO: assert zombie_refs.empty() after garbage collection
    // for zombie refs is implemented.

    delete it->second;
  }

  patches_.clear();

  for (auto it = cookie_map_.begin(); it != cookie_map_.end(); ++it) {
    delete it->second;
  }

  cookie_map_.clear();
}


int BytecodeBreakpoint::CreateBreakpoint(
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
      code_object_breakpoints->original_linedata.get());
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
  breakpoint->line = line;
  breakpoint->offset = lines_enumerator.offset();
  breakpoint->hit_callable = PythonCallback::Wrap(hit_callback);
  breakpoint->error_callback = error_callback;
  breakpoint->cookie = cookie;
  breakpoint->status = BreakpointStatus::kInactive;

  code_object_breakpoints->breakpoints.insert(
      std::make_pair(breakpoint->offset, breakpoint.get()));

  DCHECK(cookie_map_[cookie] == nullptr);
  cookie_map_[cookie] = breakpoint.release();

  return cookie;
}

void BytecodeBreakpoint::ActivateBreakpoint(int cookie) {
  if (cookie == -1) return;  // no-op if invalid cookie.

  auto it_breakpoint = cookie_map_.find(cookie);
  if (it_breakpoint == cookie_map_.end()) {
    LOG(WARNING) << "Trying to activate a breakpoint with an unknown cookie: "
                 << cookie;
    return;  // No breakpoint with this cookie.
  }

  auto it_code = patches_.find(it_breakpoint->second->code_object);
  if (it_code != patches_.end()) {
    CodeObjectBreakpoints* code = it_code->second;
    // Ensure that there is a new breakpoint that was added.
    if (it_breakpoint->second->status == BreakpointStatus::kInactive) {
      // Set breakpoint to active.
      it_breakpoint->second->status = BreakpointStatus::kActive;
      // Patch code.
      PatchCodeObject(code);
    } else {
      LOG(WARNING) << "Breakpoint with cookie: " << cookie
                   << " has already been activated";
    }
  } else {
    LOG(DFATAL) << "Missing code object";
  }
}

void BytecodeBreakpoint::ClearBreakpoint(int cookie) {
  if (cookie == -1) return;  // no-op if invalid cookie

  auto it_breakpoint = cookie_map_.find(cookie);
  if (it_breakpoint == cookie_map_.end()) {
    LOG(WARNING) << "Trying to clear a breakpoint with an unknown cookie: "
                 << cookie;
    return;  // No breakpoint with this cookie.
  }

  PythonCallback::Disable(it_breakpoint->second->hit_callable.get());

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

    // Set breakpoint as done, as it was removed from code->breakpoints map.
    it_breakpoint->second->status = BreakpointStatus::kDone;

    PatchCodeObject(code);

    if (code->breakpoints.empty() && code->zombie_refs.empty()) {
      delete it_code->second;
      patches_.erase(it_code);
    }
  } else {
    LOG(DFATAL) << "Missing code object";
  }

  delete it_breakpoint->second;
  cookie_map_.erase(it_breakpoint);
}

BreakpointStatus BytecodeBreakpoint::GetBreakpointStatus(int cookie) {
  auto it_breakpoint = cookie_map_.find(cookie);
  if (it_breakpoint == cookie_map_.end()) {
    // No breakpoint with this cookie.
    return BreakpointStatus::kUnknown;
  }

  return it_breakpoint->second->status;
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
      !PyBytes_CheckExact(data->original_code.get())) {
    LOG(ERROR) << "Code object has no code";
    return nullptr;  // Probably a built-in method or uninitialized code object.
  }

#if PY_VERSION_HEX < 0x030A0000
  data->original_linedata =
      ScopedPyObject::NewReference(code_object.get()->co_lnotab);
#else
  data->original_linedata =
      ScopedPyObject::NewReference(code_object.get()->co_linetable);
#endif

  patches_[code_object] = data.get();
  return data.release();
}


void BytecodeBreakpoint::PatchCodeObject(CodeObjectBreakpoints* code) {
  PyCodeObject* code_object = code->code_object.get();

  if (code->breakpoints.empty()) {
    code->zombie_refs.push_back(ScopedPyObject(code_object->co_consts));
    code_object->co_consts = code->original_consts.get();
    Py_INCREF(code_object->co_consts);

    code_object->co_stacksize = code->original_stacksize;

    code->zombie_refs.push_back(ScopedPyObject(code_object->co_code));
    code_object->co_code = code->original_code.get();
    VLOG(1) << "Code object " << CodeObjectDebugString(code_object)
            << " reverted to " << code_object->co_code
            << " from patched " << code->zombie_refs.back().get();
    Py_INCREF(code_object->co_code);

#if PY_VERSION_HEX < 0x030A0000
    if (code_object->co_lnotab != nullptr) {
      code->zombie_refs.push_back(ScopedPyObject(code_object->co_lnotab));
    }
    code_object->co_lnotab = code->original_linedata.get();
    Py_INCREF(code_object->co_lnotab);
#else
    if (code_object->co_linetable != nullptr) {
      code->zombie_refs.push_back(ScopedPyObject(code_object->co_linetable));
    }
    code_object->co_linetable = code->original_linedata.get();
    Py_INCREF(code_object->co_linetable);
#endif

    return;
  }

  std::vector<uint8_t> bytecode = PyBytesToByteArray(code->original_code.get());

  bool has_linedata = false;
  std::vector<uint8_t> linedata;
  if (!code->original_linedata.is_null() &&
      PyBytes_CheckExact(code->original_linedata.get())) {
    has_linedata = true;
    linedata = PyBytesToByteArray(code->original_linedata.get());
  }

  BytecodeManipulator bytecode_manipulator(
      std::move(bytecode),
      has_linedata,
      std::move(linedata));

  // Add callbacks to code object constants and patch the bytecode.
  std::vector<PyObject*> callbacks;
  callbacks.reserve(code->breakpoints.size());

  std::vector<std::function<void()>> errors;

  int const_index = PyTuple_GET_SIZE(code->original_consts.get());
  for (auto it_entry = code->breakpoints.begin();
       it_entry != code->breakpoints.end();
       ++it_entry, ++const_index) {
    // Skip breakpoint if it still hasn't been activated.
    if (it_entry->second->status == BreakpointStatus::kInactive) continue;

    int offset = it_entry->first;
    bool offset_found = true;
    const Breakpoint& breakpoint = *it_entry->second;
    DCHECK_EQ(offset, breakpoint.offset);

    callbacks.push_back(breakpoint.hit_callable.get());

    // In Python 3, since we allow upgrading of instructions to use
    // EXTENDED_ARG, the offsets for lines originally calculated might not be
    // accurate, so we need to recalculate them each insertion.
    offset_found = false;
    if (bytecode_manipulator.has_linedata()) {
      ScopedPyObject linedata(PyBytes_FromStringAndSize(
          reinterpret_cast<const char*>(bytecode_manipulator.linedata().data()),
          bytecode_manipulator.linedata().size()));
      CodeObjectLinesEnumerator lines_enumerator(code_object->co_firstlineno,
                                                 linedata.release());
      while (lines_enumerator.line_number() != breakpoint.line) {
        if (!lines_enumerator.Next()) {
          break;
        }
        offset = lines_enumerator.offset();
      }
      offset_found = lines_enumerator.line_number() == breakpoint.line;
    }

    if (!offset_found ||
        !bytecode_manipulator.InjectMethodCall(offset, const_index)) {
      LOG(WARNING) << "Failed to insert bytecode for breakpoint "
                   << breakpoint.cookie << " at line " << breakpoint.line;
      errors.push_back(breakpoint.error_callback);
      it_entry->second->status = BreakpointStatus::kError;
    } else {
      it_entry->second->status = BreakpointStatus::kActive;
    }
  }

  // Create the constants tuple, the new bytecode string and line table.
  code->zombie_refs.push_back(ScopedPyObject(code_object->co_consts));
  ScopedPyObject consts = AppendTuple(code->original_consts.get(), callbacks);
  code_object->co_consts = consts.release();

  code_object->co_stacksize = code->original_stacksize + 1;

  code->zombie_refs.push_back(ScopedPyObject(code_object->co_code));
  ScopedPyObject bytecode_string(PyBytes_FromStringAndSize(
      reinterpret_cast<const char*>(bytecode_manipulator.bytecode().data()),
      bytecode_manipulator.bytecode().size()));
  DCHECK(!bytecode_string.is_null());
  code_object->co_code = bytecode_string.release();
  VLOG(1) << "Code object " << CodeObjectDebugString(code_object)
          << " reassigned to " << code_object->co_code
          << ", original was " << code->original_code.get();

#if PY_VERSION_HEX < 0x030A0000
  if (has_linedata) {
    code->zombie_refs.push_back(ScopedPyObject(code_object->co_lnotab));
    ScopedPyObject lnotab_string(PyBytes_FromStringAndSize(
        reinterpret_cast<const char*>(bytecode_manipulator.linedata().data()),
        bytecode_manipulator.linedata().size()));
    DCHECK(!lnotab_string.is_null());
    code_object->co_lnotab = lnotab_string.release();
  }
#else
  if (has_linedata) {
    code->zombie_refs.push_back(ScopedPyObject(code_object->co_linetable));
    ScopedPyObject linetable_string(PyBytes_FromStringAndSize(
        reinterpret_cast<const char*>(bytecode_manipulator.linedata().data()),
        bytecode_manipulator.linedata().size()));
    DCHECK(!linetable_string.is_null());
    code_object->co_linetable = linetable_string.release();
  }
#endif

  // Invoke error callback after everything else is done. The callback may
  // decide to remove the breakpoint, which will change "code".
  for (auto it = errors.begin(); it != errors.end(); ++it) {
    (*it)();
  }
}

}  // namespace cdbg
}  // namespace devtools
