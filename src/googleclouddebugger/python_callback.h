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

#ifndef DEVTOOLS_CDBG_DEBUGLETS_PYTHON_PYTHON_CALLBACK_H_
#define DEVTOOLS_CDBG_DEBUGLETS_PYTHON_PYTHON_CALLBACK_H_

#include <functional>
#include "common.h"
#include "python_util.h"

namespace devtools {
namespace cdbg {

// Wraps std::function in a zero arguments Python callable.
class PythonCallback {
 public:
  PythonCallback() {}

  // Creates a zero argument Python callable that will delegate to "callback"
  // when invoked. The callback returns will always return None.
  static ScopedPyObject Wrap(std::function<void()> callback);

  // Disables any futher invocations of "callback_". The "method" is the
  // return value of "Wrap".
  static void Disable(PyObject* method);

  static PyTypeObject python_type_;

 private:
  static PyObject* Run(PyObject* self);

 private:
  // Callback to invoke or nullptr if the callback was cancelled.
  std::function<void()> callback_;

  static PyMethodDef callback_method_def_;

  DISALLOW_COPY_AND_ASSIGN(PythonCallback);
};

}  // namespace cdbg
}  // namespace devtools

#endif  // DEVTOOLS_CDBG_DEBUGLETS_PYTHON_PYTHON_CALLBACK_H_
