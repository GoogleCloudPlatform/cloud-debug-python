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

    if (code_object->co_lnotab != nullptr) {
      code->zombie_refs.push_back(ScopedPyObject(code_object->co_lnotab));
    }
    code_object->co_lnotab = code->original_lnotab.get();
    Py_INCREF(code_object->co_lnotab);

    return;
  }

  std::vector<uint8> bytecode = PyStringToByteArray(code->original_code.get());

  bool has_lnotab = false;
  std::vector<uint8> lnotab;
  if (!code->original_lnotab.is_null() &&
      PyString_CheckExact(code->original_lnotab.get())) {
    has_lnotab = true;
    lnotab = PyStringToByteArray(code->original_lnotab.get());
  }

  BytecodeManipulator bytecode_manipulator(
      std::move(bytecode),
      has_lnotab,
      std::move(lnotab));

  // Add callbacks to code object constants and patch the bytecode.
  std::vector<PyObject*> callbacks;
  callbacks.reserve(code->breakpoints.size());

  std::vector<std::function<void()>> errors;

  int const_index = PyTuple_GET_SIZE(code->original_consts.get());
  for (auto it_entry = code->breakpoints.begin();
       it_entry != code->breakpoints.end();
       ++it_entry, ++const_index) {
    const int offset = it_entry->first;
    const Breakpoint& breakpoint = *it_entry->second;
    DCHECK_EQ(offset, breakpoint.offset);

    callbacks.push_back(breakpoint.hit_callable.get());

    if (!bytecode_manipulator.InjectMethodCall(offset, const_index)) {
      LOG(WARNING) << "Failed to insert bytecode for breakpoint "
                   << breakpoint.cookie;
      errors.push_back(breakpoint.error_callback);
    }
  }

  // Create the constants tuple, the new bytecode string and line table.
  code->zombie_refs.push_back(ScopedPyObject(code_object->co_consts));
  ScopedPyObject consts = AppendTuple(code->original_consts.get(), callbacks);
  code_object->co_consts = consts.release();

  code_object->co_stacksize = code->original_stacksize + 1;

  code->zombie_refs.push_back(ScopedPyObject(code_object->co_code));
  ScopedPyObject bytecode_string(PyString_FromStringAndSize(
      reinterpret_cast<const char*>(bytecode_manipulator.bytecode().data()),
      bytecode_manipulator.bytecode().size()));
  DCHECK(!bytecode_string.is_null());
  code_object->co_code = bytecode_string.release();
  VLOG(1) << "Code object " << CodeObjectDebugString(code_object)
          << " reassigned to " << code_object->co_code
          << ", original was " << code->original_code.get();

  if (has_lnotab) {
    code->zombie_refs.push_back(ScopedPyObject(code_object->co_lnotab));
    ScopedPyObject lnotab_string(PyString_FromStringAndSize(
        reinterpret_cast<const char*>(bytecode_manipulator.lnotab().data()),
        bytecode_manipulator.lnotab().size()));
    DCHECK(!lnotab_string.is_null());
    code_object->co_lnotab = lnotab_string.release();
  }

  // Invoke error callback after everything else is done. The callback may
  // decide to remove the breakpoint, which will change "code".
  for (auto it = errors.begin(); it != errors.end(); ++it) {
    (*it)();
  }
}

}  // namespace cdbg
}  // namespace devtools


