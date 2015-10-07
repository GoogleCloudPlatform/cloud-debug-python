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

#include "python_util.h"

namespace devtools {
namespace cdbg {

// Python module object corresponding to the debuglet extension.
static PyObject* g_debuglet_module = nullptr;


CodeObjectLinesEnumerator::CodeObjectLinesEnumerator(
    PyCodeObject* code_object) {
  Initialize(code_object->co_firstlineno, code_object->co_lnotab);
}


CodeObjectLinesEnumerator::CodeObjectLinesEnumerator(
    int firstlineno,
    PyObject* lnotab) {
  Initialize(firstlineno, lnotab);
}


void CodeObjectLinesEnumerator::Initialize(
    int firstlineno,
    PyObject* lnotab) {
  offset_ = 0;
  line_number_ = firstlineno;
  remaining_entries_ = PyString_Size(lnotab) / 2;
  next_entry_ =
      reinterpret_cast<uint8*>(PyString_AsString(lnotab));

  // If the line table starts with offset 0, the first line is not
  // "code_object->co_firstlineno", but the following line.
  if ((remaining_entries_ > 0) && (next_entry_[0] == 0)) {
    Next();
  }
}


// See this URL for explanation of "co_lnotab" data structure:
// http://svn.python.org/projects/python/branches/pep-0384/Objects/lnotab_notes.txt  // NOLINT
// For reference implementation see PyCode_Addr2Line (Objects/codeobject.c).
bool CodeObjectLinesEnumerator::Next() {
  if (remaining_entries_ == 0) {
    return false;
  }

  while (true) {
    offset_ += next_entry_[0];
    line_number_ += next_entry_[1];

    bool stop = ((next_entry_[0] != 0xFF) || (next_entry_[1] != 0)) &&
                ((next_entry_[0] != 0) || (next_entry_[1] != 0xFF));

    --remaining_entries_;
    next_entry_ += 2;

    if (stop) {
      return true;
    }

    if (remaining_entries_ <= 0) {  // Corrupted line table.
      return false;
    }
  }
}


PyObject* GetDebugletModule() {
  DCHECK(g_debuglet_module != nullptr);
  return g_debuglet_module;
}


void SetDebugletModule(PyObject* module) {
  DCHECK_NE(g_debuglet_module == nullptr, module == nullptr);

  g_debuglet_module = module;
}


PyTypeObject DefaultTypeDefinition(const char* type_name) {
  return {
      PyObject_HEAD_INIT(nullptr)
      0,                          /* ob_size */
      type_name,                  /* tp_name */
      0,                          /* tp_basicsize */
      0,                          /* tp_itemsize */
      0,                          /* tp_dealloc */
      0,                          /* tp_print */
      0,                          /* tp_getattr */
      0,                          /* tp_setattr */
      0,                          /* tp_compare */
      0,                          /* tp_repr */
      0,                          /* tp_as_number */
      0,                          /* tp_as_sequence */
      0,                          /* tp_as_mapping */
      0,                          /* tp_hash */
      0,                          /* tp_call */
      0,                          /* tp_str */
      0,                          /* tp_getattro */
      0,                          /* tp_setattro */
      0,                          /* tp_as_buffer */
      Py_TPFLAGS_DEFAULT,         /* tp_flags */
      0,                          /* tp_doc */
      0,                          /* tp_traverse */
      0,                          /* tp_clear */
      0,                          /* tp_richcompare */
      0,                          /* tp_weaklistoffset */
      0,                          /* tp_iter */
      0,                          /* tp_iternext */
      0,                          /* tp_methods */
      0,                          /* tp_members */
      0,                          /* tp_getset */
      0,                          /* tp_base */
      0,                          /* tp_dict */
      0,                          /* tp_descr_get */
      0,                          /* tp_descr_set */
      0,                          /* tp_dictoffset */
      0,                          /* tp_init */
      0,                          /* tp_alloc */
      0,                          /* tp_new */
  };
}


bool RegisterPythonType(PyTypeObject* type) {
  if (PyType_Ready(type) < 0) {
    LOG(ERROR) << "Python type not ready: " << type->tp_name;
    return false;
  }

  const char* type_name = strrchr(type->tp_name, '.');
  if (type_name != nullptr) {
    ++type_name;
  } else {
    type_name = type->tp_name;
  }

  Py_INCREF(type);
  if (PyModule_AddObject(
        GetDebugletModule(),
        type_name,
        reinterpret_cast<PyObject*>(type))) {
    LOG(ERROR) << "Failed to add type object to native module";
    return false;
  }

  return true;
}


Nullable<string> ClearPythonException() {
  PyObject* exception_obj = PyErr_Occurred();
  if (exception_obj == nullptr) {
    return Nullable<string>();  // return nullptr.
  }

  // TODO(vlif): call str(exception_obj) with a verification of immutability
  // that the object state is not being altered.

  auto exception_type = reinterpret_cast<PyTypeObject*>(exception_obj->ob_type);
  string msg = exception_type->tp_name;

#ifndef NDEBUG
  PyErr_Print();
#endif  // NDEBUG

  PyErr_Clear();

  return Nullable<string>(msg);
}


PyObject* GetDebugletModuleObject(const char* key) {
  PyObject* module_dict = PyModule_GetDict(GetDebugletModule());
  if (module_dict == nullptr) {
    LOG(ERROR) << "Module has no dictionary";
    return nullptr;
  }

  PyObject* object = PyDict_GetItemString(module_dict, key);
  if (object == nullptr) {
    LOG(ERROR) << "Object " << key << " not found in module dictionary";
    return nullptr;
  }

  return object;
}


string CodeObjectDebugString(PyCodeObject* code_object) {
  if (code_object == nullptr) {
    return "<null>";
  }

  if (!PyCode_Check(code_object)) {
    return "<not a code object>";
  }

  string str;

  if ((code_object->co_name != nullptr) &&
      PyString_CheckExact(code_object->co_name)) {
    str += PyString_AS_STRING(code_object->co_name);
  } else {
    str += "<noname>";
  }

  str += ':';
  str += std::to_string(static_cast<int64>(code_object->co_firstlineno));

  if ((code_object->co_filename != nullptr) &&
      PyString_CheckExact(code_object->co_filename)) {
    str += " at ";
    str += PyString_AS_STRING(code_object->co_filename);
  }

  return str;
}

}  // namespace cdbg
}  // namespace devtools

