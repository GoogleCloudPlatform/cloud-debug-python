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

#ifndef DEVTOOLS_CDBG_DEBUGLETS_PYTHON_PYTHON_UTIL_H_
#define DEVTOOLS_CDBG_DEBUGLETS_PYTHON_PYTHON_UTIL_H_

#include <cstdint>
#include <functional>
#include <memory>

#include "common.h"
#include "nullable.h"

#define CDBG_MODULE_NAME        "cdbg_native"
#define CDBG_SCOPED_NAME(n)     CDBG_MODULE_NAME "." n

namespace devtools {
namespace cdbg {

//
// Note: all methods in this module must be called with Interpreter Lock held
// by the current thread.
//

// Wraps C++ class as Python object
struct PyObjectWrapper {
  PyObject_HEAD
  void* data;
};


// Helper class to automatically increase/decrease reference count on
// a Python object.
//
// This class can assumes the calling thread holds the Interpreter Lock. This
// is particularly important in "ScopedPyObjectT" destructor.
//
// This class is not thread safe.
template <typename TPointer>
class ScopedPyObjectT {
 public:
  // STL compatible class to compute hash of PyObject.
  class Hash {
   public:
    size_t operator() (const ScopedPyObjectT& value) const {
      return reinterpret_cast<size_t>(value.get());
    }
  };

  ScopedPyObjectT() : obj_(nullptr) {}

  // Takes over the reference.
  explicit ScopedPyObjectT(TPointer* obj) : obj_(obj) {}

  ScopedPyObjectT(const ScopedPyObjectT& other) {
    obj_ = other.obj_;
    Py_XINCREF(obj_);
  }

  ~ScopedPyObjectT() {
    // Only do anything if Python is running. If not, we get might get a
    // segfault when we try to decrement the reference count of the underlying
    // object when this destructor is run after Python itself has cleaned up.
    // https://bugs.python.org/issue17703
    if (Py_IsInitialized()) {
      reset(nullptr);
    }
  }

  static ScopedPyObjectT NewReference(TPointer* obj) {
    Py_XINCREF(obj);
    return ScopedPyObjectT(obj);
  }

  TPointer* get() const { return obj_; }

  bool is_null() const { return obj_ == nullptr; }

  ScopedPyObjectT& operator= (const ScopedPyObjectT& other) {
    if (obj_ == other.obj_) {
      return *this;
    }

    Py_XDECREF(obj_);
    obj_ = other.obj_;
    Py_XINCREF(obj_);

    return *this;
  }

  bool operator== (TPointer* other) const {
    return obj_ == other;
  }

  bool operator!= (TPointer* other) const {
    return obj_ != other;
  }

  bool operator== (const ScopedPyObjectT& other) const {
    return obj_ == other.obj_;
  }

  bool operator!= (const ScopedPyObjectT& other) const {
    return obj_ != other.obj_;
  }

  // Resets the ScopedPyObject, releasing the reference to the
  // underlying python object.  Claims the reference to the new object,
  // if it is non-NULL.
  void reset(TPointer* obj) {
    Py_XDECREF(obj_);
    obj_ = obj;
  }

  // Releases the reference to the underlying python object.  This
  // does not decrement the reference count.  This function should be
  // used when the reference is being passed to some other function,
  // class, etc.  The return value of this function is the underlying
  // Python object itself.
  TPointer* release() {
    TPointer* ret_val = obj_;
    obj_ = nullptr;
    return ret_val;
  }

  // Swaps the underlying python objects for two ScopedPyObjects.
  void swap(const ScopedPyObjectT<TPointer>& other) {
    std::swap(obj_, other.obj_);
  }

 private:
  // The underlying python object for which we hold a reference. Can be nullptr.
  TPointer* obj_;
};

typedef ScopedPyObjectT<PyObject> ScopedPyObject;
typedef ScopedPyObjectT<PyCodeObject> ScopedPyCodeObject;

// Helper class to call "PyThreadState_Swap" and revert it back to the
// previous thread in destructor.
class ScopedThreadStateSwap {
 public:
  explicit ScopedThreadStateSwap(PyThreadState* thread_state)
      : prev_thread_state_(PyThreadState_Swap(thread_state)) {}

  ~ScopedThreadStateSwap() {
    PyThreadState_Swap(prev_thread_state_);
  }

 private:
  PyThreadState* const prev_thread_state_;

  DISALLOW_COPY_AND_ASSIGN(ScopedThreadStateSwap);
};

// Enumerates code object line table.
// Usage example:
//     CodeObjectLinesEnumerator e;
//     while (enumerator.Next()) {
//       LOG(INFO) << "Line " << e.line_number() << " @ " << e.offset();
//     }
class CodeObjectLinesEnumerator {
 public:
  // Does not change reference count of "code_object".
  explicit CodeObjectLinesEnumerator(PyCodeObject* code_object);

  // Uses explicitly provided line table.
  CodeObjectLinesEnumerator(int firstlineno, PyObject* linedata);

  // Moves over to the next entry in code object line table.
  bool Next();

  // Gets the bytecode offset of the current line.
  int32_t offset() const { return offset_; }

  // Gets the current source code line number.
  int32_t line_number() const { return line_number_; }

 private:
  void Initialize(int firstlineno, PyObject* linedata);

 private:
  // Number of remaining entries in line table.
  int remaining_entries_;

  // Pointer to the next entry of line table.
  const uint8_t* next_entry_;

  // Bytecode offset of the current line.
  int32_t offset_;

  // Current source code line number
  int32_t line_number_;

  DISALLOW_COPY_AND_ASSIGN(CodeObjectLinesEnumerator);
};

template <typename TPointer>
bool operator== (TPointer* ref1, const ScopedPyObjectT<TPointer>& ref2) {
  return ref2 == ref1;
}


template <typename TPointer>
bool operator!= (TPointer* ref1, const ScopedPyObjectT<TPointer>& ref2) {
  return ref2 != ref1;
}


// Sets the debuglet's Python module object. Should only be called during
// initialization.
void SetDebugletModule(PyObject* module);

// Gets the debuglet's Python module object. Returns borrowed reference.
PyObject* GetDebugletModule();

// Default value for "PyTypeObject" with no methods. Size, initialization and
// cleanup routines are filled in by RegisterPythonType method.
PyTypeObject DefaultTypeDefinition(const char* type_name);

// Registers a custom Python type. Does not take ownership over "type".
// "type" has to stay unchanged throughout the Python module lifetime.
bool RegisterPythonType(PyTypeObject* type);

template <typename T>
int DefaultPythonTypeInit(PyObject* self, PyObject* args, PyObject* kwds) {
  PyObjectWrapper* wrapper = reinterpret_cast<PyObjectWrapper*>(self);
  wrapper->data = new T;

  return 0;
}

template <typename T>
void DefaultPythonTypeDestructor(PyObject* self) {
  PyObjectWrapper* wrapper = reinterpret_cast<PyObjectWrapper*>(self);
  delete reinterpret_cast<T*>(wrapper->data);

  PyObject_Del(self);
}

template <typename T>
bool RegisterPythonType() {
  // Set defaults for the native type.
  if (T::python_type_.tp_basicsize == 0) {
    T::python_type_.tp_basicsize = sizeof(PyObjectWrapper);
  }

  if ((T::python_type_.tp_init == nullptr) &&
      (T::python_type_.tp_dealloc == nullptr)) {
    T::python_type_.tp_init = DefaultPythonTypeInit<T>;
    T::python_type_.tp_dealloc = DefaultPythonTypeDestructor<T>;
  }

  return RegisterPythonType(&T::python_type_);
}


// Safe cast of PyObject to a native C++ object. Returns nullptr if "obj" is
// nullptr or a different type.
template <typename T>
T* py_object_cast(PyObject* obj) {
  if (obj == nullptr) {
    return nullptr;
  }

  if (Py_TYPE(obj) != &T::python_type_) {
    DCHECK(false);
    return nullptr;
  }

  return reinterpret_cast<T*>(
    reinterpret_cast<PyObjectWrapper*>(obj)->data);
}


// Creates a new native Python object.
template <typename T>
ScopedPyObject NewNativePythonObject() {
  PyObject* new_object = PyObject_New(PyObject, &T::python_type_);
  if (new_object == nullptr) {
    return ScopedPyObject();  // return nullptr.
  }

  if (T::python_type_.tp_init(new_object, nullptr, nullptr) < 0) {
    PyObject_Del(new_object);
    return ScopedPyObject();  // return nullptr.
  }

  return ScopedPyObject(new_object);
}

// Checks whether the previous call generated an exception. If not, returns
// nullptr. Otherwise formats the exception to string.
Nullable<std::string> ClearPythonException();

// Gets Python object from dictionary of a native module. Returns nullptr if not
// found. In case of success returns borrowed reference.
PyObject* GetDebugletModuleObject(const char* key);

// Formats the name and the origin of the code object for logging.
std::string CodeObjectDebugString(PyCodeObject* code_object);

// Reads Python string as a byte array. The function does not verify that
// "obj" is of a string type.
std::vector<uint8_t> PyBytesToByteArray(PyObject* obj);

// Creates a new tuple by appending "items" to elements in "tuple".
ScopedPyObject AppendTuple(
    PyObject* tuple,
    const std::vector<PyObject*>& items);

}  // namespace cdbg
}  // namespace devtools

#endif  // DEVTOOLS_CDBG_DEBUGLETS_PYTHON_PYTHON_UTIL_H_
