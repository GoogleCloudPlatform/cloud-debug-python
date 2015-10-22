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

#include "python_callback.h"

namespace devtools {
namespace cdbg {

PyTypeObject PythonCallback::python_type_ =
    DefaultTypeDefinition(CDBG_SCOPED_NAME("_Callback"));

PyMethodDef PythonCallback::callback_method_def_ = {
  const_cast<char*>("Callback"),                        // ml_name
  reinterpret_cast<PyCFunction>(PythonCallback::Run),   // ml_meth
  METH_NOARGS,                                          // ml_flags
  const_cast<char*>("")                                 // ml_doc
};

ScopedPyObject PythonCallback::Wrap(std::function<void()> callback) {
  ScopedPyObject callback_obj = NewNativePythonObject<PythonCallback>();
  py_object_cast<PythonCallback>(callback_obj.get())->callback_ = callback;

  ScopedPyObject callback_method(PyCFunction_NewEx(
      &callback_method_def_,
      callback_obj.get(),
      GetDebugletModule()));

  return callback_method;
}


void PythonCallback::Disable(PyObject* method) {
  DCHECK(PyCFunction_Check(method));

  auto instance = py_object_cast<PythonCallback>(PyCFunction_GET_SELF(method));
  DCHECK(instance);

  instance->callback_ = nullptr;
}


PyObject* PythonCallback::Run(PyObject* self) {
  auto instance = py_object_cast<PythonCallback>(self);

  if (instance->callback_ != nullptr) {
    instance->callback_();
  }

  Py_RETURN_NONE;
}

}  // namespace cdbg
}  // namespace devtools
