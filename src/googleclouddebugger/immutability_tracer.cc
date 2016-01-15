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

#include "immutability_tracer.h"

#include "python_util.h"

DEFINE_int32(
    max_expression_lines,
    10000,
    "maximum number of Python lines to allow in a single expression");

namespace devtools {
namespace cdbg {

PyTypeObject ImmutabilityTracer::python_type_ =
    DefaultTypeDefinition(CDBG_SCOPED_NAME("__ImmutabilityTracer"));

// Whitelisted C functions that we consider immutable. Some of these functions
// call Python code (like "repr"), but we can enforce immutability of these
// recursive calls.
static const char* kWhitelistedCFunctions[] = {
  "abs",
  "divmod",
  "all",
  "enumerate",
  "int",
  "ord",
  "str",
  "any",
  "isinstance",
  "pow",
  "sum",
  "issubclass",
  "super",
  "bin",
  "iter",
  "tuple",
  "bool",
  "filter",
  "len",
  "range",
  "type",
  "bytearray",
  "float",
  "list",
  "unichr",
  "format",
  "locals"
  "reduce",
  "unicode",
  "chr",
  "frozenset",
  "long",
  "vars",
  "getattr",
  "map",
  "repr",
  "xrange",
  "cmp",
  "globals",
  "max",
  "reversed",
  "zip",
  "hasattr",
  "round",
  "complex",
  "hash",
  "min",
  "set",
  "apply",
  "next",
  "dict",
  "hex",
  "object",
  "slice",
  "coerce",
  "dir",
  "id",
  "oct",
  "sorted"
};


static const char* kBlacklistedCodeObjectNames[] = {
  "__setattr__",
  "__delattr__",
  "__del__",
  "__new__",
  "__set__",
  "__delete__",
  "__call__",
  "__setitem__",
  "__delitem__",
  "__setslice__",
  "__delslice__",
};

ImmutabilityTracer::ImmutabilityTracer()
    : self_(nullptr),
      thread_state_(nullptr),
      original_thread_state_tracing_(0),
      line_count_(0),
      mutable_code_detected_(false) {
}


ImmutabilityTracer::~ImmutabilityTracer() {
  DCHECK(thread_state_ == nullptr);
}


void ImmutabilityTracer::Start(PyObject* self) {
  self_ = self;
  DCHECK(self_);

  thread_state_ = PyThreadState_GET();
  DCHECK(thread_state_);

  original_thread_state_tracing_ = thread_state_->tracing;

  // See "original_thread_state_tracing_" comment for explanation.
  thread_state_->tracing = 0;

  // We need to enable both "PyEval_SetTrace" and "PyEval_SetProfile". Enabling
  // just the former will skip over "PyTrace_C_CALL" notification.
  PyEval_SetTrace(OnTraceCallback, self_);
  PyEval_SetProfile(OnTraceCallback, self_);
}


void ImmutabilityTracer::Stop() {
  if (thread_state_ != nullptr) {
    DCHECK_EQ(thread_state_, PyThreadState_GET());

    PyEval_SetTrace(nullptr, nullptr);
    PyEval_SetProfile(nullptr, nullptr);

    // See "original_thread_state_tracing_" comment for explanation.
    thread_state_->tracing = original_thread_state_tracing_;

    thread_state_ = nullptr;
  }
}


int ImmutabilityTracer::OnTraceCallbackInternal(
    PyFrameObject* frame,
    int what,
    PyObject* arg) {
  switch (what) {
    case PyTrace_CALL:
      VerifyCodeObject(ScopedPyCodeObject::NewReference(frame->f_code));
      break;

    case PyTrace_EXCEPTION:
      break;

    case PyTrace_LINE:
      ++line_count_;
      ProcessCodeLine(frame->f_code, frame->f_lineno);
      break;

    case PyTrace_RETURN:
      break;

    case PyTrace_C_CALL:
      ++line_count_;
      ProcessCCall(arg);
      break;

    case PyTrace_C_EXCEPTION:
      break;

    case PyTrace_C_RETURN:
      break;
  }

  if (line_count_ > FLAGS_max_expression_lines) {
    LOG(INFO) << "Expression evaluation exceeded quota";
    mutable_code_detected_ = true;
  }

  if (mutable_code_detected_) {
    SetMutableCodeException();
    return -1;
  }

  return 0;
}


void ImmutabilityTracer::VerifyCodeObject(ScopedPyCodeObject code_object) {
  if (code_object == nullptr) {
    return;
  }

  if (verified_code_objects_.count(code_object) != 0) {
    return;
  }

  // Try to block expressions like "x.__setattr__('a', 1)". Python interpreter
  // doesn't generate any trace callback for calls to built-in primitives like
  // "__setattr__". Our best effort is to enumerate over all names in the code
  // object and block ones with names like "__setprop__". The user can still
  // bypass it, so this is just best effort.
  PyObject* names = code_object.get()->co_names;
  if ((names == nullptr) || !PyTuple_CheckExact(names)) {
    LOG(WARNING) << "Corrupted code object: co_names is not a valid tuple";
    mutable_code_detected_ = true;
    return;
  }

  int count = PyTuple_GET_SIZE(names);
  for (int i = 0; i != count; ++i) {
    const char* name = PyString_AsString(PyTuple_GET_ITEM(names, i));
    if (name == nullptr) {
      LOG(WARNING) << "Corrupted code object: name " << i << " is not a string";
      mutable_code_detected_ = true;
      return;
    }

    for (int j = 0; j != arraysize(kBlacklistedCodeObjectNames); ++j) {
      if (!strcmp(kBlacklistedCodeObjectNames[j], name)) {
        mutable_code_detected_ = true;
        return;
      }
    }
  }

  verified_code_objects_.insert(code_object);
}


void ImmutabilityTracer::ProcessCodeLine(
    PyCodeObject* code_object,
    int line_number) {
  int size = PyString_Size(code_object->co_code);
  const uint8* opcodes =
      reinterpret_cast<uint8*>(PyString_AsString(code_object->co_code));

  DCHECK(opcodes != nullptr);

  // Find all the code ranges mapping to the current line.
  int start_offset = -1;
  CodeObjectLinesEnumerator enumerator(code_object);
  do {
    if (start_offset != -1) {
      ProcessCodeRange(
          opcodes + start_offset,
          enumerator.offset() - start_offset);
      start_offset = -1;
    }

    if (line_number == enumerator.line_number()) {
      start_offset = enumerator.offset();
    }
  } while (enumerator.Next());

  if (start_offset != -1) {
    ProcessCodeRange(opcodes + start_offset, size - start_offset);
  }
}


void ImmutabilityTracer::ProcessCodeRange(const uint8* opcodes, int size) {
  const uint8* end = opcodes + size;
  while (opcodes < end) {
    // Read opcode.
    const uint8 opcode = *opcodes;
    ++opcodes;

    if (HAS_ARG(opcode)) {
      DCHECK_LE(opcodes + 2, end);
      opcodes += 2;

      // Opcode argument is:
      //   (static_cast<uint16>(opcodes[1]) << 8) | opcodes[0];
      // and can extend to 32 bit if EXTENDED_ARG is used.
    }

    // Notes:
    // * We allow changing local variables (i.e. STORE_FAST). Expression
    //   evaluation doesn't let changing local variables of the top frame
    //   because we use "Py_eval_input" when compiling the expression. Methods
    //   invoked by an expression can freely change local variables as it
    //   doesn't change the state of the program once the method exits.
    // * We let opcodes calling methods like "PyObject_Repr". These will either
    //   be completely executed inside Python interpreter (with no side
    //   effects), or call object method (e.g. "__repr__"). In this case the
    //   tracer will kick in and will verify that the method has no side
    //   effects.
    switch (opcode) {
      case NOP:
      case LOAD_FAST:
      case LOAD_CONST:
      case STORE_FAST:
      case POP_TOP:
      case ROT_TWO:
      case ROT_THREE:
      case ROT_FOUR:
      case DUP_TOP:
      case DUP_TOPX:
      case UNARY_POSITIVE:
      case UNARY_NEGATIVE:
      case UNARY_NOT:
      case UNARY_CONVERT:
      case UNARY_INVERT:
      case BINARY_POWER:
      case BINARY_MULTIPLY:
      case BINARY_DIVIDE:
      case BINARY_TRUE_DIVIDE:
      case BINARY_FLOOR_DIVIDE:
      case BINARY_MODULO:
      case BINARY_ADD:
      case BINARY_SUBTRACT:
      case BINARY_SUBSCR:
      case BINARY_LSHIFT:
      case BINARY_RSHIFT:
      case BINARY_AND:
      case BINARY_XOR:
      case BINARY_OR:
      case INPLACE_POWER:
      case INPLACE_MULTIPLY:
      case INPLACE_DIVIDE:
      case INPLACE_TRUE_DIVIDE:
      case INPLACE_FLOOR_DIVIDE:
      case INPLACE_MODULO:
      case INPLACE_ADD:
      case INPLACE_SUBTRACT:
      case INPLACE_LSHIFT:
      case INPLACE_RSHIFT:
      case INPLACE_AND:
      case INPLACE_XOR:
      case INPLACE_OR:
      case SLICE+0:
      case SLICE+1:
      case SLICE+2:
      case SLICE+3:
      case LOAD_LOCALS:
      case RETURN_VALUE:
      case YIELD_VALUE:
      case EXEC_STMT:
      case UNPACK_SEQUENCE:
      case LOAD_NAME:
      case LOAD_GLOBAL:
      case DELETE_FAST:
      case LOAD_DEREF:
      case BUILD_TUPLE:
      case BUILD_LIST:
      case BUILD_SET:
      case BUILD_MAP:
      case LOAD_ATTR:
      case COMPARE_OP:
      case JUMP_FORWARD:
      case POP_JUMP_IF_FALSE:
      case POP_JUMP_IF_TRUE:
      case JUMP_IF_FALSE_OR_POP:
      case JUMP_IF_TRUE_OR_POP:
      case JUMP_ABSOLUTE:
      case GET_ITER:
      case FOR_ITER:
      case BREAK_LOOP:
      case CONTINUE_LOOP:
      case SETUP_LOOP:
      case CALL_FUNCTION:
      case CALL_FUNCTION_VAR:
      case CALL_FUNCTION_KW:
      case CALL_FUNCTION_VAR_KW:
      case MAKE_FUNCTION:
      case MAKE_CLOSURE:
      case BUILD_SLICE:
      case POP_BLOCK:
        break;

      case EXTENDED_ARG:
        // Go to the next opcode. The argument is going to be incorrect,
        // but we don't really care.
        break;

      // TODO(vlif): allow changing fields of locally created objects/lists.
      case LIST_APPEND:
      case SET_ADD:
      case STORE_SLICE+0:
      case STORE_SLICE+1:
      case STORE_SLICE+2:
      case STORE_SLICE+3:
      case DELETE_SLICE+0:
      case DELETE_SLICE+1:
      case DELETE_SLICE+2:
      case DELETE_SLICE+3:
      case STORE_SUBSCR:
      case DELETE_SUBSCR:
      case STORE_NAME:
      case DELETE_NAME:
      case STORE_ATTR:
      case DELETE_ATTR:
      case STORE_DEREF:
      case STORE_MAP:
      case MAP_ADD:
        mutable_code_detected_ = true;
        return;

      case STORE_GLOBAL:
      case DELETE_GLOBAL:
        mutable_code_detected_ = true;
        return;

      case PRINT_EXPR:
      case PRINT_ITEM_TO:
      case PRINT_ITEM:
      case PRINT_NEWLINE_TO:
      case PRINT_NEWLINE:
        mutable_code_detected_ = true;
        return;

      case BUILD_CLASS:
        mutable_code_detected_ = true;
        return;

      case IMPORT_NAME:
      case IMPORT_STAR:
      case IMPORT_FROM:
      case SETUP_EXCEPT:
      case SETUP_FINALLY:
      case WITH_CLEANUP:
        mutable_code_detected_ = true;
        return;

      // TODO(vlif): allow exception handling.
      case RAISE_VARARGS:
      case END_FINALLY:
      case SETUP_WITH:
        mutable_code_detected_ = true;
        return;

      // TODO(vlif): allow closures.
      case LOAD_CLOSURE:
        mutable_code_detected_ = true;
        return;

      default:
        LOG(WARNING) << "Unknown opcode " << static_cast<uint32>(opcode);
        mutable_code_detected_ = true;
        return;
    }
  }
}


void ImmutabilityTracer::ProcessCCall(PyObject* function) {
  if (PyCFunction_Check(function)) {
    // TODO(vlif): the application code can define its own "str" function
    // that will do some evil things. Application can also override builtin
    // "str" method. If we want to protect against it, we should load pointers
    // to native functions when debugger initializes (which happens before
    // any application code had a chance to mess up with Python state). Then
    // instead of comparing names, we should look up function pointers. This
    // will also improve performance.

    auto c_function = reinterpret_cast<PyCFunctionObject*>(function);
    const char* name = c_function->m_ml->ml_name;

    for (uint32 i = 0; i < arraysize(kWhitelistedCFunctions); ++i) {
      if (!strcmp(name, kWhitelistedCFunctions[i])) {
        return;
      }
    }

    LOG(INFO) << "Calling native function " << name << " is not allowed";

    mutable_code_detected_ = true;
    return;
  }

  LOG(WARNING) << "Unknown argument for C function call";

  mutable_code_detected_ = true;
}


void ImmutabilityTracer::SetMutableCodeException() {
  // TODO(vlif): use custom type for this exception. This way we can provide
  // a more detailed error message.
  PyErr_SetString(
      PyExc_SystemError,
      "Only immutable methods can be called from expressions");
}

}  // namespace cdbg
}  // namespace devtools

