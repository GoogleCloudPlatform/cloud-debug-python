/**
 * Copyright 2023 Google Inc. All Rights Reserved.
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
#include "src/googleclouddebugger/bytecode_manipulator.h"

#include <cstdint>
#include <gtest/gtest.h>

namespace devtools {
namespace cdbg {

static std::string FormatOpcode(uint8_t opcode) {
  switch (opcode) {
    case POP_TOP: return "POP_TOP";
    case ROT_TWO: return "ROT_TWO";
    case ROT_THREE: return "ROT_THREE";
    case DUP_TOP: return "DUP_TOP";
    case NOP: return "NOP";
    case UNARY_POSITIVE: return "UNARY_POSITIVE";
    case UNARY_NEGATIVE: return "UNARY_NEGATIVE";
    case UNARY_NOT: return "UNARY_NOT";
    case UNARY_INVERT: return "UNARY_INVERT";
    case BINARY_POWER: return "BINARY_POWER";
    case BINARY_MULTIPLY: return "BINARY_MULTIPLY";
    case BINARY_MODULO: return "BINARY_MODULO";
    case BINARY_ADD: return "BINARY_ADD";
    case BINARY_SUBTRACT: return "BINARY_SUBTRACT";
    case BINARY_SUBSCR: return "BINARY_SUBSCR";
    case BINARY_FLOOR_DIVIDE: return "BINARY_FLOOR_DIVIDE";
    case BINARY_TRUE_DIVIDE: return "BINARY_TRUE_DIVIDE";
    case INPLACE_FLOOR_DIVIDE: return "INPLACE_FLOOR_DIVIDE";
    case INPLACE_TRUE_DIVIDE: return "INPLACE_TRUE_DIVIDE";
    case INPLACE_ADD: return "INPLACE_ADD";
    case INPLACE_SUBTRACT: return "INPLACE_SUBTRACT";
    case INPLACE_MULTIPLY: return "INPLACE_MULTIPLY";
    case INPLACE_MODULO: return "INPLACE_MODULO";
    case STORE_SUBSCR: return "STORE_SUBSCR";
    case DELETE_SUBSCR: return "DELETE_SUBSCR";
    case BINARY_LSHIFT: return "BINARY_LSHIFT";
    case BINARY_RSHIFT: return "BINARY_RSHIFT";
    case BINARY_AND: return "BINARY_AND";
    case BINARY_XOR: return "BINARY_XOR";
    case BINARY_OR: return "BINARY_OR";
    case INPLACE_POWER: return "INPLACE_POWER";
    case GET_ITER: return "GET_ITER";
    case PRINT_EXPR: return "PRINT_EXPR";
    case INPLACE_LSHIFT: return "INPLACE_LSHIFT";
    case INPLACE_RSHIFT: return "INPLACE_RSHIFT";
    case INPLACE_AND: return "INPLACE_AND";
    case INPLACE_XOR: return "INPLACE_XOR";
    case INPLACE_OR: return "INPLACE_OR";
    case RETURN_VALUE: return "RETURN_VALUE";
    case IMPORT_STAR: return "IMPORT_STAR";
    case YIELD_VALUE: return "YIELD_VALUE";
    case POP_BLOCK: return "POP_BLOCK";
#if PY_VERSION_HEX <= 0x03080000
    case END_FINALLY: return "END_FINALLY";
#endif
    case STORE_NAME: return "STORE_NAME";
    case DELETE_NAME: return "DELETE_NAME";
    case UNPACK_SEQUENCE: return "UNPACK_SEQUENCE";
    case FOR_ITER: return "FOR_ITER";
    case LIST_APPEND: return "LIST_APPEND";
    case STORE_ATTR: return "STORE_ATTR";
    case DELETE_ATTR: return "DELETE_ATTR";
    case STORE_GLOBAL: return "STORE_GLOBAL";
    case DELETE_GLOBAL: return "DELETE_GLOBAL";
    case LOAD_CONST: return "LOAD_CONST";
    case LOAD_NAME: return "LOAD_NAME";
    case BUILD_TUPLE: return "BUILD_TUPLE";
    case BUILD_LIST: return "BUILD_LIST";
    case BUILD_SET: return "BUILD_SET";
    case BUILD_MAP: return "BUILD_MAP";
    case LOAD_ATTR: return "LOAD_ATTR";
    case COMPARE_OP: return "COMPARE_OP";
    case IMPORT_NAME: return "IMPORT_NAME";
    case IMPORT_FROM: return "IMPORT_FROM";
    case JUMP_FORWARD: return "JUMP_FORWARD";
    case JUMP_IF_FALSE_OR_POP: return "JUMP_IF_FALSE_OR_POP";
    case JUMP_IF_TRUE_OR_POP: return "JUMP_IF_TRUE_OR_POP";
    case JUMP_ABSOLUTE: return "JUMP_ABSOLUTE";
    case POP_JUMP_IF_FALSE: return "POP_JUMP_IF_FALSE";
    case POP_JUMP_IF_TRUE: return "POP_JUMP_IF_TRUE";
    case LOAD_GLOBAL: return "LOAD_GLOBAL";
    case SETUP_FINALLY: return "SETUP_FINALLY";
    case LOAD_FAST: return "LOAD_FAST";
    case STORE_FAST: return "STORE_FAST";
    case DELETE_FAST: return "DELETE_FAST";
    case RAISE_VARARGS: return "RAISE_VARARGS";
    case CALL_FUNCTION: return "CALL_FUNCTION";
    case MAKE_FUNCTION: return "MAKE_FUNCTION";
    case BUILD_SLICE: return "BUILD_SLICE";
    case LOAD_CLOSURE: return "LOAD_CLOSURE";
    case LOAD_DEREF: return "LOAD_DEREF";
    case STORE_DEREF: return "STORE_DEREF";
    case CALL_FUNCTION_KW: return "CALL_FUNCTION_KW";
    case SETUP_WITH: return "SETUP_WITH";
    case EXTENDED_ARG: return "EXTENDED_ARG";
    case SET_ADD: return "SET_ADD";
    case MAP_ADD: return "MAP_ADD";
#if PY_VERSION_HEX < 0x03080000
    case BREAK_LOOP: return "BREAK_LOOP";
    case CONTINUE_LOOP: return "CONTINUE_LOOP";
    case SETUP_LOOP: return "SETUP_LOOP";
    case SETUP_EXCEPT: return "SETUP_EXCEPT";
#endif
    case DUP_TOP_TWO: return "DUP_TOP_TWO";
    case BINARY_MATRIX_MULTIPLY: return "BINARY_MATRIX_MULTIPLY";
    case INPLACE_MATRIX_MULTIPLY: return "INPLACE_MATRIX_MULTIPLY";
    case GET_AITER: return "GET_AITER";
    case GET_ANEXT: return "GET_ANEXT";
    case BEFORE_ASYNC_WITH: return "BEFORE_ASYNC_WITH";
    case GET_YIELD_FROM_ITER: return "GET_YIELD_FROM_ITER";
    case LOAD_BUILD_CLASS: return "LOAD_BUILD_CLASS";
    case YIELD_FROM: return "YIELD_FROM";
    case GET_AWAITABLE: return "GET_AWAITABLE";
#if PY_VERSION_HEX <= 0x03080000
    case WITH_CLEANUP_START: return "WITH_CLEANUP_START";
    case WITH_CLEANUP_FINISH: return "WITH_CLEANUP_FINISH";
#endif
    case SETUP_ANNOTATIONS: return "SETUP_ANNOTATIONS";
    case POP_EXCEPT: return "POP_EXCEPT";
    case UNPACK_EX: return "UNPACK_EX";
#if PY_VERSION_HEX < 0x03070000
    case STORE_ANNOTATION: return "STORE_ANNOTATION";
#endif
    case CALL_FUNCTION_EX: return "CALL_FUNCTION_EX";
    case LOAD_CLASSDEREF: return "LOAD_CLASSDEREF";
#if PY_VERSION_HEX <= 0x03080000
    case BUILD_LIST_UNPACK: return "BUILD_LIST_UNPACK";
    case BUILD_MAP_UNPACK: return "BUILD_MAP_UNPACK";
    case BUILD_MAP_UNPACK_WITH_CALL: return "BUILD_MAP_UNPACK_WITH_CALL";
    case BUILD_TUPLE_UNPACK: return "BUILD_TUPLE_UNPACK";
    case BUILD_SET_UNPACK: return "BUILD_SET_UNPACK";
#endif
    case SETUP_ASYNC_WITH: return "SETUP_ASYNC_WITH";
    case FORMAT_VALUE: return "FORMAT_VALUE";
    case BUILD_CONST_KEY_MAP: return "BUILD_CONST_KEY_MAP";
    case BUILD_STRING: return "BUILD_STRING";
#if PY_VERSION_HEX <= 0x03080000
    case BUILD_TUPLE_UNPACK_WITH_CALL: return "BUILD_TUPLE_UNPACK_WITH_CALL";
#endif
#if PY_VERSION_HEX >= 0x03070000
    case LOAD_METHOD: return "LOAD_METHOD";
    case CALL_METHOD: return "CALL_METHOD";
#endif
#if PY_VERSION_HEX >= 0x03080000 && PY_VERSION_HEX < 0x03090000
    case BEGIN_FINALLY: return "BEGIN_FINALLY":
    case POP_FINALLY: return "POP_FINALLY";
#endif
#if PY_VERSION_HEX >= 0x03080000
    case ROT_FOUR: return "ROT_FOUR";
    case END_ASYNC_FOR: return "END_ASYNC_FOR";
#endif
#if PY_VERSION_HEX >= 0x03080000 && PY_VERSION_HEX < 0x03090000
    // Added in Python 3.8 and removed in 3.9
    case CALL_FINALLY: return "CALL_FINALLY";
#endif
#if PY_VERSION_HEX >= 0x03090000
    case RERAISE: return "RERAISE";
    case WITH_EXCEPT_START: return "WITH_EXCEPT_START";
    case LOAD_ASSERTION_ERROR: return "LOAD_ASSERTION_ERROR";
    case LIST_TO_TUPLE: return "LIST_TO_TUPLE";
    case IS_OP: return "IS_OP";
    case CONTAINS_OP: return "CONTAINS_OP";
    case JUMP_IF_NOT_EXC_MATCH: return "JUMP_IF_NOT_EXC_MATCH";
    case LIST_EXTEND: return "LIST_EXTEND";
    case SET_UPDATE: return "SET_UPDATE";
    case DICT_MERGE: return "DICT_MERGE";
    case DICT_UPDATE: return "DICT_UPDATE";
#endif

    default: return std::to_string(static_cast<int>(opcode));
  }
}

static std::string FormatBytecode(const std::vector<uint8_t>& bytecode,
                                  int indent) {
  std::string rc;
  int remaining_argument_bytes = 0;
  for (auto it = bytecode.begin(); it != bytecode.end(); ++it) {
    std::string line;
    if (remaining_argument_bytes == 0) {
      line = FormatOpcode(*it);
      remaining_argument_bytes = 1;
    } else {
      line = std::to_string(static_cast<int>(*it));
      --remaining_argument_bytes;
    }

    if (it < bytecode.end() - 1) {
      line += ',';
    }

    line.resize(20, ' ');
    line += "// offset ";
    line += std::to_string(it - bytecode.begin());
    line += '.';

    rc += std::string(indent, ' ');
    rc += line;

    if (it < bytecode.end() - 1) {
      rc += '\n';
    }
  }

  return rc;
}

static void VerifyBytecode(const BytecodeManipulator& bytecode_manipulator,
                           std::vector<uint8_t> expected_bytecode) {
  EXPECT_EQ(expected_bytecode, bytecode_manipulator.bytecode())
      << "Actual bytecode:\n"
      << "      {\n"
      << FormatBytecode(bytecode_manipulator.bytecode(), 10) << "\n"
      << "      }";
}

static void VerifyLineNumbersTable(
    const BytecodeManipulator& bytecode_manipulator,
    std::vector<uint8_t> expected_linedata) {
  // Convert to integers to better logging by EXPECT_EQ.
  std::vector<int> expected(expected_linedata.begin(), expected_linedata.end());
  std::vector<int> actual(
      bytecode_manipulator.linedata().begin(),
      bytecode_manipulator.linedata().end());

  EXPECT_EQ(expected, actual);
}

TEST(BytecodeManipulatorTest, EmptyBytecode) {
  BytecodeManipulator instance({}, false, {});
  EXPECT_FALSE(instance.InjectMethodCall(0, 0));
}


TEST(BytecodeManipulatorTest, HasLineNumbersTable) {
  BytecodeManipulator instance1({}, false, {});
  EXPECT_FALSE(instance1.has_linedata());

  BytecodeManipulator instance2({}, true, {});
  EXPECT_TRUE(instance2.has_linedata());
}




TEST(BytecodeManipulatorTest, InsertionSimple) {
  BytecodeManipulator instance({ NOP, 0, RETURN_VALUE, 0 }, false, {});
  ASSERT_TRUE(instance.InjectMethodCall(2, 47));

  VerifyBytecode(
      instance,
      {
          NOP,                // offset 0.
          0,                  // offset 1.
          LOAD_CONST,         // offset 4.
          47,                 // offset 5.
          CALL_FUNCTION,      // offset 6.
          0,                  // offset 7.
          POP_TOP,            // offset 8.
          0,                  // offset 9.
          RETURN_VALUE,       // offset 10.
          0                   // offset 11.
      });
}


TEST(BytecodeManipulatorTest, InsertionExtended) {
  BytecodeManipulator instance({ NOP, 0, RETURN_VALUE, 0 }, false, {});
  ASSERT_TRUE(instance.InjectMethodCall(2, 0x12345678));

  VerifyBytecode(
      instance,
      {
          NOP,                // offset 0.
          0,                  // offset 1.
          EXTENDED_ARG,       // offset 2.
          0x12,               // offset 3.
          EXTENDED_ARG,       // offset 2.
          0x34,               // offset 3.
          EXTENDED_ARG,       // offset 2.
          0x56,               // offset 3.
          LOAD_CONST,         // offset 4.
          0x78,               // offset 5.
          CALL_FUNCTION,      // offset 6.
          0,                  // offset 7.
          POP_TOP,            // offset 8.
          0,                  // offset 9.
          RETURN_VALUE,       // offset 10.
          0                   // offset 11.
      });
}


TEST(BytecodeManipulatorTest, InsertionBeginning) {
  BytecodeManipulator instance({ NOP, 0, RETURN_VALUE, 0 }, false, {});
  ASSERT_TRUE(instance.InjectMethodCall(0, 47));

  VerifyBytecode(
      instance,
      {
          LOAD_CONST,         // offset 0.
          47,                 // offset 1.
          CALL_FUNCTION,      // offset 2.
          0,                  // offset 3.
          POP_TOP,            // offset 4.
          0,                  // offset 5.
          NOP,                // offset 6.
          0,                  // offset 7.
          RETURN_VALUE,       // offset 8.
          0                   // offset 9.
      });
}


TEST(BytecodeManipulatorTest, InsertionOffsetUpdates) {
  BytecodeManipulator instance(
      {
          JUMP_FORWARD,
          12,
          NOP,
          0,
          JUMP_ABSOLUTE,
          34,
      },
      false,
      {});
  ASSERT_TRUE(instance.InjectMethodCall(2, 47));

  VerifyBytecode(
      instance,
      {
          JUMP_FORWARD,       // offset 0.
          12 + 6,             // offset 1.
          LOAD_CONST,         // offset 2.
          47,                 // offset 3.
          CALL_FUNCTION,      // offset 4.
          0,                  // offset 5.
          POP_TOP,            // offset 6.
          0,                  // offset 7.
          NOP,                // offset 8.
          0,                  // offset 9.
          JUMP_ABSOLUTE,      // offset 10.
          34 + 6              // offset 11.
      });
}


TEST(BytecodeManipulatorTest, InsertionExtendedOffsetUpdates) {
  BytecodeManipulator instance(
      {
          EXTENDED_ARG,
          12,
          EXTENDED_ARG,
          34,
          EXTENDED_ARG,
          56,
          JUMP_FORWARD,
          78,
          NOP,
          0,
          EXTENDED_ARG,
          98,
          EXTENDED_ARG,
          76,
          EXTENDED_ARG,
          54,
          JUMP_ABSOLUTE,
          32
      },
      false,
      {});
  ASSERT_TRUE(instance.InjectMethodCall(8, 11));

  VerifyBytecode(
      instance,
      {
          EXTENDED_ARG,       // offset 0.
          12,                 // offset 1.
          EXTENDED_ARG,       // offset 2.
          34,                 // offset 3.
          EXTENDED_ARG,       // offset 4.
          56,                 // offset 5.
          JUMP_FORWARD,       // offset 6.
          78 + 6,             // offset 7.
          LOAD_CONST,         // offset 8.
          11,                 // offset 9.
          CALL_FUNCTION,      // offset 10.
          0,                  // offset 11.
          POP_TOP,            // offset 12.
          0,                  // offset 13.
          NOP,                // offset 14.
          0,                  // offset 15.
          EXTENDED_ARG,       // offset 16.
          98,                 // offset 17.
          EXTENDED_ARG,       // offset 18.
          76,                 // offset 19.
          EXTENDED_ARG,       // offset 20.
          54,                 // offset 21.
          JUMP_ABSOLUTE,      // offset 22.
          32 + 6              // offset 23.
      });
}


TEST(BytecodeManipulatorTest, InsertionDeltaOffsetNoUpdate) {
  BytecodeManipulator instance(
      {
          JUMP_FORWARD,
          2,
          NOP,
          0,
          RETURN_VALUE,
          0,
          JUMP_FORWARD,
          2,
      },
      false, {});
  ASSERT_TRUE(instance.InjectMethodCall(4, 99));

  VerifyBytecode(
      instance,
      {
          JUMP_FORWARD,       // offset 0.
          2,                  // offset 1.
          NOP,                // offset 2.
          0,                  // offset 3.
          LOAD_CONST,         // offset 4.
          99,                 // offset 5.
          CALL_FUNCTION,      // offset 6.
          0,                  // offset 7.
          POP_TOP,            // offset 8.
          0,                  // offset 9.
          RETURN_VALUE,       // offset 10.
          0,                  // offset 11.
          JUMP_FORWARD,       // offset 12.
          2                   // offset 13.
      });
}


TEST(BytecodeManipulatorTest, InsertionAbsoluteOffsetNoUpdate) {
  BytecodeManipulator instance(
      {
          JUMP_ABSOLUTE,
          2,
          RETURN_VALUE,
          0
      },
      false,
      {});
  ASSERT_TRUE(instance.InjectMethodCall(2, 99));

  VerifyBytecode(
      instance,
      {
          JUMP_ABSOLUTE,      // offset 0.
          2,                  // offset 1.
          LOAD_CONST,         // offset 2.
          99,                 // offset 3.
          CALL_FUNCTION,      // offset 4.
          0,                  // offset 5.
          POP_TOP,            // offset 6.
          0,                  // offset 7.
          RETURN_VALUE,       // offset 8.
          0                   // offset 9.
      });
}


TEST(BytecodeManipulatorTest, InsertionOffsetUneededExtended) {
  BytecodeManipulator instance(
      { EXTENDED_ARG, 0, JUMP_FORWARD, 2, NOP, 0 },
      false,
      {});
  ASSERT_TRUE(instance.InjectMethodCall(4, 11));

  VerifyBytecode(
      instance,
      {
          EXTENDED_ARG,       // offset 0.
          0,                  // offset 1.
          JUMP_FORWARD,       // offset 2.
          8,                  // offset 3.
          LOAD_CONST,         // offset 4.
          11,                 // offset 5.
          CALL_FUNCTION,      // offset 6.
          0,                  // offset 7.
          POP_TOP,            // offset 8.
          0,                  // offset 9.
          NOP,                // offset 10.
          0                   // offset 11.
      });
}


TEST(BytecodeManipulatorTest, InsertionOffsetUpgradeExtended) {
  BytecodeManipulator instance({ JUMP_ABSOLUTE, 250 , NOP, 0 }, false, {});
  ASSERT_TRUE(instance.InjectMethodCall(2, 11));

  VerifyBytecode(
      instance,
      {
          EXTENDED_ARG,       // offset 0.
          1,                  // offset 1.
          JUMP_ABSOLUTE,      // offset 2.
          2,                  // offset 3.
          LOAD_CONST,         // offset 4.
          11,                 // offset 5.
          CALL_FUNCTION,      // offset 6.
          0,                  // offset 7.
          POP_TOP,            // offset 8.
          0,                  // offset 9.
          NOP,                // offset 10.
          0                   // offset 11.
      });
}


TEST(BytecodeManipulatorTest, InsertionOffsetUpgradeExtendedTwice) {
  BytecodeManipulator instance(
      { JUMP_ABSOLUTE, 248, JUMP_ABSOLUTE, 250, NOP, 0 },
      false,
      {});
  ASSERT_TRUE(instance.InjectMethodCall(4, 12));

  VerifyBytecode(
      instance,
      {
          EXTENDED_ARG,       // offset 0.
          1,                  // offset 1.
          JUMP_ABSOLUTE,      // offset 2.
          2,                  // offset 3.
          EXTENDED_ARG,       // offset 4.
          1,                  // offset 5.
          JUMP_ABSOLUTE,      // offset 6.
          4,                  // offset 7.
          LOAD_CONST,         // offset 8.
          12,                 // offset 9.
          CALL_FUNCTION,      // offset 10.
          0,                  // offset 11.
          POP_TOP,            // offset 12.
          0,                  // offset 13.
          NOP,                // offset 14.
          0                   // offset 15.
      });
}


TEST(BytecodeManipulatorTest, InsertionBadInstruction) {
  BytecodeManipulator instance(
      { NOP, 0, NOP, 0, LOAD_CONST },
      false,
      {});
  EXPECT_FALSE(instance.InjectMethodCall(2, 0));
}


TEST(BytecodeManipulatorTest, InsertionNegativeOffset) {
  BytecodeManipulator instance({ NOP, 0, RETURN_VALUE, 0 }, false, {});
  EXPECT_FALSE(instance.InjectMethodCall(-1, 0));
}


TEST(BytecodeManipulatorTest, InsertionOutOfRangeOffset) {
  BytecodeManipulator instance({ NOP, 0, RETURN_VALUE, 0 }, false, {});
  EXPECT_FALSE(instance.InjectMethodCall(4, 0));
}


TEST(BytecodeManipulatorTest, InsertionMidInstruction) {
  BytecodeManipulator instance(
      { NOP, 0, LOAD_CONST, 0, NOP, 0 },
      false,
      {});

  EXPECT_FALSE(instance.InjectMethodCall(1, 0));
  EXPECT_FALSE(instance.InjectMethodCall(3, 0));
  EXPECT_FALSE(instance.InjectMethodCall(5, 0));
}


TEST(BytecodeManipulatorTest, InsertionTooManyUpgrades) {
  BytecodeManipulator instance(
      {
          JUMP_ABSOLUTE, 250,
          JUMP_ABSOLUTE, 250,
          JUMP_ABSOLUTE, 250,
          JUMP_ABSOLUTE, 250,
          JUMP_ABSOLUTE, 250,
          JUMP_ABSOLUTE, 250,
          JUMP_ABSOLUTE, 250,
          JUMP_ABSOLUTE, 250,
          JUMP_ABSOLUTE, 250,
          JUMP_ABSOLUTE, 250,
          NOP, 0
      },
      false,
      {});
  EXPECT_FALSE(instance.InjectMethodCall(20, 0));
}


TEST(BytecodeManipulatorTest, IncompleteBytecodeInsert) {
  BytecodeManipulator instance({ NOP, 0, LOAD_CONST }, false, {});
  EXPECT_FALSE(instance.InjectMethodCall(2, 0));
}


TEST(BytecodeManipulatorTest, IncompleteBytecodeAppend) {
  BytecodeManipulator instance(
      { YIELD_VALUE, 0, NOP, 0, LOAD_CONST },
      false, {});
  EXPECT_FALSE(instance.InjectMethodCall(4, 0));
}


TEST(BytecodeManipulatorTest, LineNumbersTableUpdateBeginning) {
  BytecodeManipulator instance(
      { NOP, 0, RETURN_VALUE, 0 },
      true,
      { 2, 1, 2, 1 });
  ASSERT_TRUE(instance.InjectMethodCall(0, 99));

  VerifyLineNumbersTable(instance, { 8, 1, 2, 1 });
}


TEST(BytecodeManipulatorTest, LineNumbersTableUpdateLineBoundary) {
  BytecodeManipulator instance(
      { NOP, 0, RETURN_VALUE, 0 },
      true,
      { 0, 1, 2, 1, 2, 1 });
  ASSERT_TRUE(instance.InjectMethodCall(2, 99));

  VerifyLineNumbersTable(instance, { 0, 1, 2, 1, 8, 1 });
}


TEST(BytecodeManipulatorTest, LineNumbersTableUpdateMidLine) {
  BytecodeManipulator instance(
      { NOP, 0, NOP, 0, RETURN_VALUE, 0 },
      true,
      { 0, 1, 4, 1 });
  ASSERT_TRUE(instance.InjectMethodCall(2, 99));

  VerifyLineNumbersTable(instance, { 0, 1, 10, 1 });
}


TEST(BytecodeManipulatorTest, LineNumbersTablePastEnd) {
  BytecodeManipulator instance(
      { NOP, 0, NOP, 0, NOP, 0, RETURN_VALUE, 0 },
      true,
      { 0, 1 });
  ASSERT_TRUE(instance.InjectMethodCall(6, 99));

  VerifyLineNumbersTable(instance, { 0, 1 });
}


TEST(BytecodeManipulatorTest, LineNumbersTableUpgradeExtended) {
  BytecodeManipulator instance(
      { JUMP_ABSOLUTE, 250, RETURN_VALUE, 0 },
      true,
      { 2, 1, 2, 1 });
  ASSERT_TRUE(instance.InjectMethodCall(2, 99));

  VerifyLineNumbersTable(instance, { 4, 1, 8, 1 });
}


TEST(BytecodeManipulatorTest, LineNumbersTableOverflow) {
  std::vector<uint8_t> bytecode(300, 0);
  BytecodeManipulator instance(
      bytecode,
      true,
      { 254, 1 });
  ASSERT_TRUE(instance.InjectMethodCall(2, 99));

#if PY_VERSION_HEX >= 0x030A0000
  VerifyLineNumbersTable(instance, { 254, 0, 6, 1 });
#else
  VerifyLineNumbersTable(instance, { 255, 0, 5, 1 });
#endif
}


TEST(BytecodeManipulatorTest, SuccessAppend) {
  BytecodeManipulator instance(
      { YIELD_VALUE, 0, LOAD_CONST, 0, NOP, 0 },
      false,
      {});
  ASSERT_TRUE(instance.InjectMethodCall(2, 57));

  VerifyBytecode(
      instance,
      {
          YIELD_VALUE,        // offset 0.
          0,                  // offset 1.
          JUMP_ABSOLUTE,      // offset 2.
          6,                  // offset 3.
          NOP,                // offset 4.
          0,                  // offset 5.
          LOAD_CONST,         // offset 6.
          57,                 // offset 7.
          CALL_FUNCTION,      // offset 8.
          0,                  // offset 9.
          POP_TOP,            // offset 10.
          0,                  // offset 11.
          LOAD_CONST,         // offset 12.
          0,                  // offset 13.
          JUMP_ABSOLUTE,      // offset 14.
          4                   // offset 15.
      });
}


TEST(BytecodeManipulatorTest, SuccessAppendYieldFrom) {
  BytecodeManipulator instance(
      { YIELD_FROM, 0, LOAD_CONST, 0, NOP, 0 },
      false,
      {});
  ASSERT_TRUE(instance.InjectMethodCall(2, 57));

  VerifyBytecode(
      instance,
      {
          YIELD_FROM,         // offset 0.
          0,                  // offset 1.
          JUMP_ABSOLUTE,      // offset 2.
          6,                  // offset 3.
          NOP,                // offset 4.
          0,                  // offset 5.
          LOAD_CONST,         // offset 6.
          57,                 // offset 7.
          CALL_FUNCTION,      // offset 8.
          0,                  // offset 9.
          POP_TOP,            // offset 10.
          0,                  // offset 11.
          LOAD_CONST,         // offset 12.
          0,                  // offset 13.
          JUMP_ABSOLUTE,      // offset 14.
          4                   // offset 15.
      });
}


TEST(BytecodeManipulatorTest, AppendExtraPadding) {
  BytecodeManipulator instance(
      {
          YIELD_VALUE,
          0,
          EXTENDED_ARG,
          15,
          EXTENDED_ARG,
          16,
          EXTENDED_ARG,
          17,
          LOAD_CONST,
          18,
          RETURN_VALUE,
          0
      },
      false, {});
  ASSERT_TRUE(instance.InjectMethodCall(2, 0x7273));

  VerifyBytecode(
      instance,
      {
          YIELD_VALUE,        // offset 0.
          0,                  // offset 1.
          JUMP_ABSOLUTE,      // offset 2.
          12,                 // offset 3.
          NOP,                // offset 4. Args for NOP do not matter.
          9,                  // offset 5.
          NOP,                // offset 6.
          9,                  // offset 7.
          NOP,                // offset 8.
          9,                  // offset 9.
          RETURN_VALUE,       // offset 10.
          0,                  // offset 11.
          EXTENDED_ARG,       // offset 12.
          0x72,               // offset 13.
          LOAD_CONST,         // offset 14.
          0x73,               // offset 15.
          CALL_FUNCTION,      // offset 16.
          0,                  // offset 17.
          POP_TOP,            // offset 18.
          0,                  // offset 19.
          EXTENDED_ARG,       // offset 20.
          15,                 // offset 21.
          EXTENDED_ARG,       // offset 22.
          16,                 // offset 23.
          EXTENDED_ARG,       // offset 24.
          17,                 // offset 25.
          LOAD_CONST,         // offset 26.
          18,                 // offset 27.
          JUMP_ABSOLUTE,      // offset 28.
          10                  // offset 29.
      });
}


TEST(BytecodeManipulatorTest, AppendToEnd) {
  std::vector<uint8_t> bytecode = {YIELD_VALUE, 0};
  // Case where trampoline requires 4 bytes to write.
  bytecode.resize(300);
  BytecodeManipulator instance(bytecode, false, {});

  // This scenario could be supported in theory, but it's not. The purpose of
  // this test case is to verify there are no crashes or corruption.
  ASSERT_FALSE(instance.InjectMethodCall(298, 0x12));
}


TEST(BytecodeManipulatorTest, NoSpaceForTrampoline) {
  const std::vector<uint8_t> test_cases[] = {
    {YIELD_VALUE, 0, YIELD_VALUE, 0, NOP, 0},
    {YIELD_VALUE, 0, FOR_ITER, 0, NOP, 0},
    {YIELD_VALUE, 0, JUMP_FORWARD, 0, NOP, 0},
#if PY_VERSION_HEX < 0x03080000
    {YIELD_VALUE, 0, SETUP_LOOP, 0, NOP, 0},
#endif
    {YIELD_VALUE, 0, SETUP_FINALLY, 0, NOP, 0},
#if PY_VERSION_HEX < 0x03080000
    {YIELD_VALUE, 0, SETUP_LOOP, 0, NOP, 0},
    {YIELD_VALUE, 0, SETUP_EXCEPT, 0, NOP, 0},
#endif
#if PY_VERSION_HEX >= 0x03080000 && PY_VERSION_HEX < 0x03090000
    {YIELD_VALUE, 0, CALL_FINALLY, 0, NOP, 0},
#endif
  };

  for (const auto& test_case : test_cases) {
    BytecodeManipulator instance(test_case, false, {});
    EXPECT_FALSE(instance.InjectMethodCall(2, 0))
        << "Input:\n"
        << FormatBytecode(test_case, 4) << "\n"
        << "Unexpected output:\n"
        << FormatBytecode(instance.bytecode(), 4);
  }

  // Case where trampoline requires 4 bytes to write.
  std::vector<uint8_t> bytecode(300, 0);
  bytecode[0] = YIELD_VALUE;
  bytecode[2] = NOP;
  bytecode[4] = YIELD_VALUE;
  BytecodeManipulator instance(bytecode, false, {});
  ASSERT_FALSE(instance.InjectMethodCall(2, 0x12));
}

// Tests that we don't allow jumping into the middle of the space reserved for
// the trampoline. See the comments in AppendMethodCall() in
// bytecode_manipulator.cc.
TEST(BytecodeManipulatorTest, JumpMidRelocatedInstructions) {
  std::vector<uint8_t> test_cases[] = {
    {YIELD_VALUE, 0, FOR_ITER, 2, LOAD_CONST, 0},
    {YIELD_VALUE, 0, JUMP_FORWARD, 2, LOAD_CONST, 0},
    {YIELD_VALUE, 0, SETUP_FINALLY, 2, LOAD_CONST, 0},
    {YIELD_VALUE, 0, SETUP_WITH, 2, LOAD_CONST, 0},
    {YIELD_VALUE, 0, SETUP_FINALLY, 2, LOAD_CONST, 0},
    {YIELD_VALUE, 0, JUMP_IF_FALSE_OR_POP, 6, LOAD_CONST, 0},
    {YIELD_VALUE, 0, JUMP_IF_TRUE_OR_POP, 6, LOAD_CONST, 0},
    {YIELD_VALUE, 0, JUMP_ABSOLUTE, 6, LOAD_CONST, 0},
    {YIELD_VALUE, 0, POP_JUMP_IF_FALSE, 6, LOAD_CONST, 0},
    {YIELD_VALUE, 0, POP_JUMP_IF_TRUE, 6, LOAD_CONST, 0},
#if PY_VERSION_HEX < 0x03080000
    {YIELD_VALUE, 0, SETUP_LOOP, 2, LOAD_CONST, 0},
    {YIELD_VALUE, 0, CONTINUE_LOOP, 6, LOAD_CONST, 0},
#endif
  };

  for (auto& test_case : test_cases) {
    // Case where trampoline requires 4 bytes to write.
    test_case.resize(300);
    BytecodeManipulator instance(test_case, false, {});
    EXPECT_FALSE(instance.InjectMethodCall(4, 0))
        << "Input:\n"
        << FormatBytecode(test_case, 4) << "\n"
        << "Unexpected output:\n"
        << FormatBytecode(instance.bytecode(), 4);
  }
}


// Test that we allow jumping to the start of the space reserved for the
// trampoline.
TEST(BytecodeManipulatorTest, JumpStartOfRelocatedInstructions) {
  const std::vector<uint8_t> test_cases[] = {
      {YIELD_VALUE, 0, FOR_ITER, 0, LOAD_CONST, 0},
      {YIELD_VALUE, 0, SETUP_WITH, 0, LOAD_CONST, 0},
      {YIELD_VALUE, 0, JUMP_ABSOLUTE, 4, LOAD_CONST, 0}};

  for (const auto& test_case : test_cases) {
    BytecodeManipulator instance(test_case, false, {});
    EXPECT_TRUE(instance.InjectMethodCall(4, 0))
        << "Input:\n" << FormatBytecode(test_case, 4);
  }
}


// Test that we allow jumping after the space reserved for the trampoline.
TEST(BytecodeManipulatorTest, JumpAfterRelocatedInstructions) {
  const std::vector<uint8_t> test_cases[] = {
      {YIELD_VALUE, 0, FOR_ITER, 2, LOAD_CONST, 0, NOP, 0},
      {YIELD_VALUE, 0, SETUP_WITH, 2, LOAD_CONST, 0, NOP, 0},
      {YIELD_VALUE, 0, JUMP_ABSOLUTE, 6, LOAD_CONST, 0, NOP, 0}};

  for (const auto& test_case : test_cases) {
    BytecodeManipulator instance(test_case, false, {});
    EXPECT_TRUE(instance.InjectMethodCall(4, 0))
        << "Input:\n" << FormatBytecode(test_case, 4);
  }
}


TEST(BytecodeManipulatorTest, InsertionRevertOnFailure) {
  const std::vector<uint8_t> input{JUMP_FORWARD, 0, NOP, 0, JUMP_ABSOLUTE, 2};

  BytecodeManipulator instance(input, false, {});
  ASSERT_FALSE(instance.InjectMethodCall(1, 47));

  VerifyBytecode(instance, input);
}


}  // namespace cdbg
}  // namespace devtools
