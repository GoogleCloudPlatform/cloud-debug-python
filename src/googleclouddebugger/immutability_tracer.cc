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
  "locals",
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
  int size = PyBytes_Size(code_object->co_code);
  const uint8* opcodes =
      reinterpret_cast<uint8*>(PyBytes_AsString(code_object->co_code));

  DCHECK(opcodes != nullptr);

  // Find all the code ranges mapping to the current line.
  int start_offset = -1;
  CodeObjectLinesEnumerator enumerator(code_object);
  do {
    if (start_offset != -1) {
      ProcessCodeRange(
          opcodes,
          opcodes + start_offset,
          enumerator.offset() - start_offset);
      start_offset = -1;
    }

    if (line_number == enumerator.line_number()) {
      start_offset = enumerator.offset();
    }
  } while (enumerator.Next());

  if (start_offset != -1) {
    ProcessCodeRange(opcodes, opcodes + start_offset, size - start_offset);
  }
}

enum OpcodeMutableStatus {
  OPCODE_MUTABLE,
  OPCODE_NOT_MUTABLE,
  OPCODE_MAYBE_MUTABLE
};

static OpcodeMutableStatus IsOpcodeMutable(const uint8 opcode) {
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
    case POP_TOP:
    case ROT_TWO:
    case ROT_THREE:
    case DUP_TOP:
    case NOP:
    case UNARY_POSITIVE:
    case UNARY_NEGATIVE:
    case UNARY_INVERT:
    case BINARY_POWER:
    case BINARY_MULTIPLY:
    case BINARY_MODULO:
    case BINARY_ADD:
    case BINARY_SUBTRACT:
    case BINARY_SUBSCR:
    case BINARY_FLOOR_DIVIDE:
    case BINARY_TRUE_DIVIDE:
    case INPLACE_FLOOR_DIVIDE:
    case INPLACE_TRUE_DIVIDE:
    case INPLACE_ADD:
    case INPLACE_SUBTRACT:
    case INPLACE_MULTIPLY:
    case INPLACE_MODULO:
    case BINARY_LSHIFT:
    case BINARY_RSHIFT:
    case BINARY_AND:
    case BINARY_XOR:
    case INPLACE_POWER:
    case GET_ITER:
    case INPLACE_LSHIFT:
    case INPLACE_RSHIFT:
    case INPLACE_AND:
    case INPLACE_XOR:
    case INPLACE_OR:
    case RETURN_VALUE:
    case YIELD_VALUE:
    case POP_BLOCK:
    case UNPACK_SEQUENCE:
    case FOR_ITER:
    case LOAD_CONST:
    case LOAD_NAME:
    case BUILD_TUPLE:
    case BUILD_LIST:
    case BUILD_SET:
    case BUILD_MAP:
    case LOAD_ATTR:
    case COMPARE_OP:
    case JUMP_FORWARD:
    case JUMP_IF_FALSE_OR_POP:
    case JUMP_IF_TRUE_OR_POP:
    case POP_JUMP_IF_TRUE:
    case POP_JUMP_IF_FALSE:
    case LOAD_GLOBAL:
    case LOAD_FAST:
    case STORE_FAST:
    case DELETE_FAST:
    case CALL_FUNCTION:
    case MAKE_FUNCTION:
    case BUILD_SLICE:
    case LOAD_DEREF:
    case CALL_FUNCTION_KW:
    case EXTENDED_ARG:
#if PY_VERSION_HEX < 0x03080000
    // These were all removed in Python 3.8.
    case BREAK_LOOP:
    case CONTINUE_LOOP:
    case SETUP_LOOP:
#endif
#if PY_MAJOR_VERSION >= 3
    case DUP_TOP_TWO:
    case BINARY_MATRIX_MULTIPLY:
    case INPLACE_MATRIX_MULTIPLY:
    case GET_YIELD_FROM_ITER:
    case YIELD_FROM:
    case UNPACK_EX:
    case CALL_FUNCTION_EX:
    case LOAD_CLASSDEREF:
    case BUILD_LIST_UNPACK:
    case BUILD_MAP_UNPACK:
    case BUILD_MAP_UNPACK_WITH_CALL:
    case BUILD_TUPLE_UNPACK:
    case BUILD_SET_UNPACK:
    case FORMAT_VALUE:
    case BUILD_CONST_KEY_MAP:
    case BUILD_STRING:
    case BUILD_TUPLE_UNPACK_WITH_CALL:
#if PY_VERSION_HEX >= 0x03070000
    // Added in Python 3.7.
    case LOAD_METHOD:
    case CALL_METHOD:
#endif
#if PY_VERSION_HEX >= 0x03080000
    // Added back in Python 3.8 (was in 2.7 as well)
    case ROT_FOUR:
#endif
#else
    case ROT_FOUR:
    case DUP_TOPX:
    case UNARY_NOT:
    case UNARY_CONVERT:
    case BINARY_DIVIDE:
    case BINARY_OR:
    case INPLACE_DIVIDE:
    case SLICE+0:
    case SLICE+1:
    case SLICE+2:
    case SLICE+3:
    case LOAD_LOCALS:
    case EXEC_STMT:
    case JUMP_ABSOLUTE:
    case CALL_FUNCTION_VAR:
    case CALL_FUNCTION_VAR_KW:
    case MAKE_CLOSURE:
#endif
      return OPCODE_NOT_MUTABLE;

    case PRINT_EXPR:
    case STORE_GLOBAL:
    case DELETE_GLOBAL:
    case IMPORT_STAR:
    case IMPORT_NAME:
    case IMPORT_FROM:
    case SETUP_FINALLY:
    // TODO: allow changing fields of locally created objects/lists.
    case STORE_SUBSCR:
    case DELETE_SUBSCR:
    case STORE_NAME:
    case DELETE_NAME:
    case STORE_ATTR:
    case DELETE_ATTR:
    case LIST_APPEND:
    case SET_ADD:
    case MAP_ADD:
    case STORE_DEREF:
    // TODO: allow exception handling
    case RAISE_VARARGS:
    case END_FINALLY:
    case SETUP_WITH:
    // TODO: allow closures
    case LOAD_CLOSURE:
#if PY_VERSION_HEX < 0x03080000
    // Removed in Python 3.8.
    case SETUP_EXCEPT:
#endif
#if PY_MAJOR_VERSION >= 3
    case GET_AITER:
    case GET_ANEXT:
    case BEFORE_ASYNC_WITH:
    case LOAD_BUILD_CLASS:
    case GET_AWAITABLE:
    case WITH_CLEANUP_START:
    case WITH_CLEANUP_FINISH:
    case SETUP_ANNOTATIONS:
    case POP_EXCEPT:
#if PY_VERSION_HEX < 0x03070000
    // Removed in Python 3.7.
    case STORE_ANNOTATION:
#endif
    case DELETE_DEREF:
    case SETUP_ASYNC_WITH:
#if PY_VERSION_HEX >= 0x03080000
    // Added in Python 3.8.
    case BEGIN_FINALLY:
    case END_ASYNC_FOR:
    case CALL_FINALLY:
    case POP_FINALLY:
#endif
#else
    case STORE_SLICE+0:
    case STORE_SLICE+1:
    case STORE_SLICE+2:
    case STORE_SLICE+3:
    case DELETE_SLICE+0:
    case DELETE_SLICE+1:
    case DELETE_SLICE+2:
    case DELETE_SLICE+3:
    case STORE_MAP:
    case PRINT_ITEM_TO:
    case PRINT_ITEM:
    case PRINT_NEWLINE_TO:
    case PRINT_NEWLINE:
    case BUILD_CLASS:
    case WITH_CLEANUP:
#endif
      return OPCODE_MUTABLE;

    default:
      return OPCODE_MAYBE_MUTABLE;
  }
}

void ImmutabilityTracer::ProcessCodeRange(const uint8* code_start,
                                          const uint8* opcodes, int size) {
  const uint8* end = opcodes + size;
  while (opcodes < end) {
    // Read opcode.
    const uint8 opcode = *opcodes;
    switch (IsOpcodeMutable(opcode)) {
      case OPCODE_NOT_MUTABLE:
        // We don't worry about the sizes of instructions with EXTENDED_ARG.
        // The argument does not really matter and so EXTENDED_ARGs can be
        // treated as just another instruction with an opcode.
#if PY_MAJOR_VERSION >= 3
        opcodes += 2;
#else
        opcodes += HAS_ARG(opcode) ? 3 : 1;
#endif
        DCHECK_LE(opcodes, end);
        break;

      case OPCODE_MAYBE_MUTABLE:
#if PY_MAJOR_VERSION >= 3
        if (opcode == JUMP_ABSOLUTE) {
          // Check for a jump to itself, which happens in "while True: pass".
          // The tracer won't call our tracing function unless there is a jump
          // backwards, or we reached a new line. In this case neither of those
          // ever happens, so we can't rely on our tracing function to detect
          // infinite loops.
          // In this case EXTENDED_ARG doesn't matter either because if this
          // instruction had one it would jump backwards and be caught tracing.
          if (opcodes - code_start == opcodes[1]) {
            mutable_code_detected_ = true;
            return;
          }
          opcodes += 2;
          DCHECK_LE(opcodes, end);
          break;
        }
#endif
        LOG(WARNING) << "Unknown opcode " << static_cast<uint32>(opcode);
        mutable_code_detected_ = true;
        return;

      case OPCODE_MUTABLE:
        mutable_code_detected_ = true;
        return;
    }
  }
}


void ImmutabilityTracer::ProcessCCall(PyObject* function) {
  if (PyCFunction_Check(function)) {
    // TODO: the application code can define its own "str" function
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
  // TODO: use custom type for this exception. This way we can provide
  // a more detailed error message.
  PyErr_SetString(
      PyExc_SystemError,
      "Only immutable methods can be called from expressions");
}

}  // namespace cdbg
}  // namespace devtools
