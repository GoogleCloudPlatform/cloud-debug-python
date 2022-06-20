"""Unit test for collector module."""

import copy
import datetime
import inspect
import logging
import os
import time
from unittest import mock

from absl.testing import absltest

from googleclouddebugger import collector
from googleclouddebugger import labels

LOGPOINT_PAUSE_MSG = (
    'LOGPOINT: Logpoint is paused due to high log rate until log '
    'quota is restored')


def CaptureCollectorWithDefaultLocation(definition,
                                        data_visibility_policy=None):
  """Makes a LogCollector with a default location.

  Args:
    definition: the rest of the breakpoint definition
    data_visibility_policy: optional visibility policy

  Returns:
    A LogCollector
  """
  definition['location'] = {'path': 'collector_test.py', 'line': 10}
  return collector.CaptureCollector(definition, data_visibility_policy)


def LogCollectorWithDefaultLocation(definition):
  """Makes a LogCollector with a default location.

  Args:
    definition: the rest of the breakpoint definition

  Returns:
    A LogCollector
  """
  definition['location'] = {'path': 'collector_test.py', 'line': 10}
  return collector.LogCollector(definition)


class CaptureCollectorTest(absltest.TestCase):
  """Unit test for capture collector."""

  def tearDown(self):
    collector.CaptureCollector.pretty_printers = []

  def testCallStackUnlimitedFrames(self):
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.max_frames = 1000
    self._collector.Collect(inspect.currentframe())

    self.assertGreater(len(self._collector.breakpoint['stackFrames']), 1)
    self.assertLess(len(self._collector.breakpoint['stackFrames']), 100)

  def testCallStackLimitedFrames(self):
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.max_frames = 2
    self._collector.Collect(inspect.currentframe())

    self.assertLen(self._collector.breakpoint['stackFrames'], 2)

    top_frame = self._collector.breakpoint['stackFrames'][0]
    self.assertEqual('CaptureCollectorTest.testCallStackLimitedFrames',
                     top_frame['function'])
    self.assertIn('collector_test.py', top_frame['location']['path'])
    self.assertGreater(top_frame['location']['line'], 1)

    frame_below = self._collector.breakpoint['stackFrames'][1]
    frame_below_line = inspect.currentframe().f_back.f_lineno
    self.assertEqual(frame_below_line, frame_below['location']['line'])

  def testCallStackLimitedExpandedFrames(self):

    def CountLocals(frame):
      return len(frame['arguments']) + len(frame['locals'])

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.max_frames = 3
    self._collector.max_expand_frames = 2
    self._collector.Collect(inspect.currentframe())

    frames = self._collector.breakpoint['stackFrames']
    self.assertLen(frames, 3)
    self.assertGreater(CountLocals(frames[0]), 0)
    self.assertGreater(CountLocals(frames[1]), 1)
    self.assertEqual(0, CountLocals(frames[2]))

  def testSimpleArguments(self):

    def Method(unused_a, unused_b):
      self._collector.Collect(inspect.currentframe())
      top_frame = self._collector.breakpoint['stackFrames'][0]
      self.assertListEqual([{
          'name': 'unused_a',
          'value': '158',
          'type': 'int'
      }, {
          'name': 'unused_b',
          'value': "'hello'",
          'type': 'str'
      }], top_frame['arguments'])
      self.assertEqual('Method', top_frame['function'])

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    Method(158, 'hello')

  def testMethodWithFirstArgumentNamedSelf(self):
    this = self

    def Method(self, unused_a, unused_b):  # pylint: disable=unused-argument
      this._collector.Collect(inspect.currentframe())
      top_frame = this._collector.breakpoint['stackFrames'][0]
      this.assertListEqual([{
          'name': 'self',
          'value': "'world'",
          'type': 'str'
      }, {
          'name': 'unused_a',
          'value': '158',
          'type': 'int'
      }, {
          'name': 'unused_b',
          'value': "'hello'",
          'type': 'str'
      }], top_frame['arguments'])
      # This is the incorrect function name, but we are validating that no
      # exceptions are thrown here.
      this.assertEqual('str.Method', top_frame['function'])

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    Method('world', 158, 'hello')

  def testMethodWithArgumentNamedSelf(self):
    this = self

    def Method(unused_a, unused_b, self):  # pylint: disable=unused-argument
      this._collector.Collect(inspect.currentframe())
      top_frame = this._collector.breakpoint['stackFrames'][0]
      this.assertListEqual([{
          'name': 'unused_a',
          'value': '158',
          'type': 'int'
      }, {
          'name': 'unused_b',
          'value': "'hello'",
          'type': 'str'
      }, {
          'name': 'self',
          'value': "'world'",
          'type': 'str'
      }], top_frame['arguments'])
      this.assertEqual('Method', top_frame['function'])

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    Method(158, 'hello', 'world')

  def testClassMethod(self):
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())
    top_frame = self._collector.breakpoint['stackFrames'][0]
    self.assertListEqual([{
        'name': 'self',
        'varTableIndex': 1
    }], top_frame['arguments'])
    self.assertEqual('CaptureCollectorTest.testClassMethod',
                     top_frame['function'])

  def testClassMethodWithOptionalArguments(self):

    def Method(unused_a, unused_optional='notneeded'):
      self._collector.Collect(inspect.currentframe())
      top_frame = self._collector.breakpoint['stackFrames'][0]
      self.assertListEqual([{
          'name': 'unused_a',
          'varTableIndex': 1
      }, {
          'name': 'unused_optional',
          'value': "'notneeded'",
          'type': 'str'
      }], top_frame['arguments'])
      self.assertEqual('Method', top_frame['function'])

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    Method(self)

  def testClassMethodWithPositionalArguments(self):

    def Method(*unused_pos):
      self._collector.Collect(inspect.currentframe())
      top_frame = self._collector.breakpoint['stackFrames'][0]
      self.assertListEqual([{
          'name': 'unused_pos',
          'type': 'tuple',
          'members': [{
              'name': '[0]',
              'value': '1',
              'type': 'int'
          }]
      }], top_frame['arguments'])
      self.assertEqual('Method', top_frame['function'])

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    Method(1)

  def testClassMethodWithKeywords(self):

    def Method(**unused_kwd):
      self._collector.Collect(inspect.currentframe())
      top_frame = self._collector.breakpoint['stackFrames'][0]
      self.assertCountEqual([{
          'name': "'first'",
          'value': '1',
          'type': 'int'
      }, {
          'name': "'second'",
          'value': '2',
          'type': 'int'
      }], top_frame['arguments'][0]['members'])
      self.assertEqual('Method', top_frame['function'])

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    Method(first=1, second=2)

  def testNoLocalVariables(self):
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())
    top_frame = self._collector.breakpoint['stackFrames'][0]
    self.assertEmpty(top_frame['locals'])
    self.assertEqual('CaptureCollectorTest.testNoLocalVariables',
                     top_frame['function'])

  def testRuntimeError(self):

    class BadDict(dict):

      def __init__(self, d):
        d['foo'] = 'bar'
        super(BadDict, self).__init__(d)

      def __getattribute__(self, attr):
        raise RuntimeError('Bogus error')

    class BadType(object):

      def __init__(self):
        self.__dict__ = BadDict(self.__dict__)

    unused_a = BadType()

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    var_a = self._Pack(self._LocalByName('unused_a'))
    self.assertDictEqual(
        {
            'name': 'unused_a',
            'status': {
                'isError': True,
                'refersTo': 'VARIABLE_VALUE',
                'description': {
                    'format': 'Failed to capture variable: $0',
                    'parameters': ['Bogus error']
                },
            }
        }, var_a)

  def testBadDictionary(self):

    class BadDict(dict):

      def items(self):
        raise AttributeError('attribute error')

    class BadType(object):

      def __init__(self):
        self.good = 1
        self.bad = BadDict()

    unused_a = BadType()

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    var_a = self._Pack(self._LocalByName('unused_a'))
    members = var_a['members']
    self.assertLen(members, 2)
    self.assertIn({'name': 'good', 'value': '1', 'type': 'int'}, members)
    self.assertIn(
        {
            'name': 'bad',
            'status': {
                'isError': True,
                'refersTo': 'VARIABLE_VALUE',
                'description': {
                    'format': 'Failed to capture variable: $0',
                    'parameters': ['attribute error']
                },
            }
        }, members)

  def testLocalVariables(self):
    unused_a = 8
    unused_b = True
    unused_nothing = None
    unused_s = 'hippo'

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())
    top_frame = self._collector.breakpoint['stackFrames'][0]
    self.assertLen(top_frame['arguments'], 1)  # just self.
    self.assertCountEqual([{
        'name': 'unused_a',
        'value': '8',
        'type': 'int'
    }, {
        'name': 'unused_b',
        'value': 'True',
        'type': 'bool'
    }, {
        'name': 'unused_nothing',
        'value': 'None'
    }, {
        'name': 'unused_s',
        'value': "'hippo'",
        'type': 'str'
    }], top_frame['locals'])

  def testLocalVariablesWithBlacklist(self):
    unused_a = collector.LineNoFilter()
    unused_b = 5

    # Side effect logic for the mock data visibility object
    def IsDataVisible(name):
      path_prefix = 'googleclouddebugger.collector.'
      if name == path_prefix + 'LineNoFilter':
        return (False, 'data blocked')
      return (True, None)

    mock_policy = mock.MagicMock()
    mock_policy.IsDataVisible.side_effect = IsDataVisible

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'},
                                                          mock_policy)
    self._collector.Collect(inspect.currentframe())
    top_frame = self._collector.breakpoint['stackFrames'][0]
    # Should be blocked
    self.assertIn(
        {
            'name': 'unused_a',
            'status': {
                'description': {
                    'format': 'data blocked'
                },
                'refersTo': 'VARIABLE_NAME',
                'isError': True
            }
        }, top_frame['locals'])
    # Should not be blocked
    self.assertIn({
        'name': 'unused_b',
        'value': '5',
        'type': 'int'
    }, top_frame['locals'])

  def testWatchedExpressionsBlacklisted(self):

    class TestClass(object):

      def __init__(self):
        self.a = 5

    unused_a = TestClass()

    # Side effect logic for the mock data visibility object
    def IsDataVisible(name):
      if name == 'collector_test.TestClass':
        return (False, 'data blocked')
      return (True, None)

    mock_policy = mock.MagicMock()
    mock_policy.IsDataVisible.side_effect = IsDataVisible

    self._collector = CaptureCollectorWithDefaultLocation(
        {
            'id': 'BP_ID',
            'expressions': ['unused_a', 'unused_a.a']
        }, mock_policy)
    self._collector.Collect(inspect.currentframe())
    # Class should be blocked
    self.assertIn(
        {
            'name': 'unused_a',
            'status': {
                'description': {
                    'format': 'data blocked'
                },
                'refersTo': 'VARIABLE_NAME',
                'isError': True
            }
        }, self._collector.breakpoint['evaluatedExpressions'])
    # TODO: Explicit member SHOULD also be blocked but this is
    # currently not implemented.  After fixing the implementation, change
    # the test below to assert that it's blocked too.
    self.assertIn({
        'name': 'unused_a.a',
        'type': 'int',
        'value': '5'
    }, self._collector.breakpoint['evaluatedExpressions'])

  def testLocalsNonTopFrame(self):

    def Method():
      self._collector.Collect(inspect.currentframe())
      self.assertListEqual([{
          'name': 'self',
          'varTableIndex': 1
      }], self._collector.breakpoint['stackFrames'][1]['arguments'])
      self.assertCountEqual([{
          'name': 'unused_a',
          'value': '47',
          'type': 'int'
      }, {
          'name': 'Method',
          'value': 'function Method'
      }], self._collector.breakpoint['stackFrames'][1]['locals'])

    unused_a = 47
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    Method()

  def testDictionaryMaxDepth(self):
    d = {}
    t = d
    for _ in range(10):
      t['inner'] = {}
      t = t['inner']

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.default_capture_limits.max_depth = 3
    self._collector.Collect(inspect.currentframe())
    self.assertDictEqual(
        {
            'name':
                'd',
            'type':
                'dict',
            'members': [{
                'name': "'inner'",
                'type': 'dict',
                'members': [{
                    'name': "'inner'",
                    'varTableIndex': 0
                }]
            }]
        }, self._LocalByName('d'))

  def testVectorMaxDepth(self):
    l = []
    t = l
    for _ in range(10):
      t.append([])
      t = t[0]

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.default_capture_limits.max_depth = 3
    self._collector.Collect(inspect.currentframe())
    self.assertDictEqual(
        {
            'name':
                'l',
            'type':
                'list',
            'members': [{
                'name': '[0]',
                'type': 'list',
                'members': [{
                    'name': '[0]',
                    'varTableIndex': 0
                }]
            }]
        }, self._LocalByName('l'))

  def testStringTrimming(self):
    unused_s = '123456789'
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.default_capture_limits.max_value_len = 8
    self._collector.Collect(inspect.currentframe())
    self.assertListEqual([{
        'name': 'unused_s',
        'value': "'12345678...",
        'type': 'str'
    }], self._collector.breakpoint['stackFrames'][0]['locals'])

  def testBytearrayTrimming(self):
    unused_bytes = bytearray(range(20))
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.default_capture_limits.max_value_len = 20
    self._collector.Collect(inspect.currentframe())
    self.assertListEqual([{
        'name': 'unused_bytes',
        'value': r"bytearray(b'\x00\x01\...",
        'type': 'bytearray'
    }], self._collector.breakpoint['stackFrames'][0]['locals'])

  def testObject(self):

    class MyClass(object):

      def __init__(self):
        self.a = 1
        self.b = 2

    unused_my = MyClass()
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())
    var_index = self._LocalByName('unused_my')['varTableIndex']
    self.assertEqual(
        __name__ + '.MyClass',
        self._collector.breakpoint['variableTable'][var_index]['type'])
    self.assertCountEqual([{
        'name': 'a',
        'value': '1',
        'type': 'int'
    }, {
        'name': 'b',
        'value': '2',
        'type': 'int'
    }], self._collector.breakpoint['variableTable'][var_index]['members'])

  def testBufferFullLocalRef(self):

    class MyClass(object):

      def __init__(self, data):
        self.data = data

    def Method():
      unused_m1 = MyClass('1' * 10000)
      unused_m2 = MyClass('2' * 10000)
      unused_m3 = MyClass('3' * 10000)
      unused_m4 = MyClass('4' * 10000)
      unused_m5 = MyClass('5' * 10000)
      unused_m6 = MyClass('6' * 10000)

      self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
      self._collector.max_frames = 1
      self._collector.max_size = 48000
      self._collector.default_capture_limits.max_value_len = 10009
      self._collector.Collect(inspect.currentframe())

      # Verify that 5 locals fit and 1 is out of buffer.
      count = {True: 0, False: 0}  # captured, not captured
      for local in self._collector.breakpoint['stackFrames'][0]['locals']:
        var_index = local['varTableIndex']
        self.assertLess(var_index,
                        len(self._collector.breakpoint['variableTable']))
        if local['name'].startswith('unused_m'):
          count[var_index != 0] += 1
      self.assertDictEqual({True: 5, False: 1}, count)

    Method()

  def testBufferFullDictionaryRef(self):

    class MyClass(object):

      def __init__(self, data):
        self.data = data

    def Method():
      unused_d1 = {'a': MyClass('1' * 10000)}
      unused_d2 = {'b': MyClass('2' * 10000)}

      self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
      self._collector.max_frames = 1
      self._collector.max_size = 9000
      self._collector.default_capture_limits.max_value_len = 10009
      self._collector.Collect(inspect.currentframe())

      # Verify that one of {d1,d2} could fit and the other didn't.
      var_indexes = [
          self._LocalByName(n)['members'][0]['varTableIndex'] == 0
          for n in ['unused_d1', 'unused_d2']
      ]
      self.assertEqual(1, sum(var_indexes))

    Method()

  def testClassCrossReference(self):

    class MyClass(object):
      pass

    m1 = MyClass()
    m2 = MyClass()
    m1.other = m2
    m2.other = m1

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    m1_var_index = self._LocalByName('m1')['varTableIndex']
    m2_var_index = self._LocalByName('m2')['varTableIndex']

    var_table = self._collector.breakpoint['variableTable']
    self.assertDictEqual(
        {
            'type': __name__ + '.MyClass',
            'members': [{
                'name': 'other',
                'varTableIndex': m1_var_index
            }]
        }, var_table[m2_var_index])
    self.assertDictEqual(
        {
            'type': __name__ + '.MyClass',
            'members': [{
                'name': 'other',
                'varTableIndex': m2_var_index
            }]
        }, var_table[m1_var_index])

  def testCaptureVector(self):
    unused_my_list = [1, 2, 3, 4, 5]
    unused_my_slice = unused_my_list[1:4]

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    self.assertDictEqual(
        {
            'name':
                'unused_my_list',
            'type':
                'list',
            'members': [{
                'name': '[0]',
                'value': '1',
                'type': 'int'
            }, {
                'name': '[1]',
                'value': '2',
                'type': 'int'
            }, {
                'name': '[2]',
                'value': '3',
                'type': 'int'
            }, {
                'name': '[3]',
                'value': '4',
                'type': 'int'
            }, {
                'name': '[4]',
                'value': '5',
                'type': 'int'
            }]
        }, self._LocalByName('unused_my_list'))
    self.assertDictEqual(
        {
            'name':
                'unused_my_slice',
            'type':
                'list',
            'members': [{
                'name': '[0]',
                'value': '2',
                'type': 'int'
            }, {
                'name': '[1]',
                'value': '3',
                'type': 'int'
            }, {
                'name': '[2]',
                'value': '4',
                'type': 'int'
            }]
        }, self._LocalByName('unused_my_slice'))

  def testCaptureDictionary(self):
    unused_my_dict = {
        'first': 1,
        3.14: 'pi',
        (5, 6): 7,
        frozenset([5, 6]): 'frozen',
        'vector': ['odin', 'dva', 'tri'],
        'inner': {
            1: 'one'
        },
        'empty': {}
    }

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    frozenset_name = 'frozenset({5, 6})'
    self.assertCountEqual([{
        'name': "'first'",
        'value': '1',
        'type': 'int'
    }, {
        'name': '3.14',
        'value': "'pi'",
        'type': 'str'
    }, {
        'name': '(5, 6)',
        'value': '7',
        'type': 'int'
    }, {
        'name': frozenset_name,
        'value': "'frozen'",
        'type': 'str'
    }, {
        'name':
            "'vector'",
        'type':
            'list',
        'members': [{
            'name': '[0]',
            'value': "'odin'",
            'type': 'str'
        }, {
            'name': '[1]',
            'value': "'dva'",
            'type': 'str'
        }, {
            'name': '[2]',
            'value': "'tri'",
            'type': 'str'
        }]
    }, {
        'name': "'inner'",
        'type': 'dict',
        'members': [{
            'name': '1',
            'value': "'one'",
            'type': 'str'
        }]
    }, {
        'name':
            "'empty'",
        'type':
            'dict',
        'members': [{
            'status': {
                'refersTo': 'VARIABLE_NAME',
                'description': {
                    'format': 'Empty dictionary'
                }
            }
        }]
    }],
                          self._LocalByName('unused_my_dict')['members'])

  def testEscapeDictionaryKey(self):
    unused_dict = {}
    unused_dict[u'\xe0'] = u'\xe0'
    unused_dict['\x88'] = '\x88'

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    unicode_type = 'str'
    unicode_name = "'\xe0'"
    unicode_value = "'\xe0'"

    self.assertCountEqual([{
        'type': 'str',
        'name': "'\\x88'",
        'value': "'\\x88'"
    }, {
        'type': unicode_type,
        'name': unicode_name,
        'value': unicode_value
    }],
                          self._LocalByName('unused_dict')['members'])

  def testOversizedList(self):
    unused_big_list = ['x'] * 10000

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    members = self._LocalByName('unused_big_list')['members']

    self.assertLen(members, 26)
    self.assertDictEqual({
        'name': '[7]',
        'value': "'x'",
        'type': 'str'
    }, members[7])
    self.assertDictEqual(
        {
            'status': {
                'refersTo': 'VARIABLE_VALUE',
                'description': {
                    'format': (
                        'Only first $0 items were captured. Use in an expression'
                        ' to see all items.'),
                    'parameters': ['25']
                }
            }
        }, members[25])

  def testOversizedDictionary(self):
    unused_big_dict = {'item' + str(i): i**2 for i in range(26)}

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    members = self._LocalByName('unused_big_dict')['members']

    self.assertLen(members, 26)
    self.assertDictEqual(
        {
            'status': {
                'refersTo': 'VARIABLE_VALUE',
                'description': {
                    'format': (
                        'Only first $0 items were captured. Use in an expression'
                        ' to see all items.'),
                    'parameters': ['25']
                }
            }
        }, members[25])

  def testEmptyDictionary(self):
    unused_empty_dict = {}

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    self.assertEqual(
        {
            'name':
                'unused_empty_dict',
            'type':
                'dict',
            'members': [{
                'status': {
                    'refersTo': 'VARIABLE_NAME',
                    'description': {
                        'format': 'Empty dictionary'
                    }
                }
            }]
        }, self._LocalByName('unused_empty_dict'))

  def testEmptyCollection(self):
    for unused_c, object_type in [([], 'list'), ((), 'tuple'), (set(), 'set')]:
      self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
      self._collector.Collect(inspect.currentframe())

      self.assertEqual(
          {
              'name':
                  'unused_c',
              'type':
                  object_type,
              'members': [{
                  'status': {
                      'refersTo': 'VARIABLE_NAME',
                      'description': {
                          'format': 'Empty collection'
                      }
                  }
              }]
          }, self._Pack(self._LocalByName('unused_c')))

  def testEmptyClass(self):

    class EmptyObject(object):
      pass

    unused_empty_object = EmptyObject()

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    self.assertEqual(
        {
            'name':
                'unused_empty_object',
            'type':
                __name__ + '.EmptyObject',
            'members': [{
                'status': {
                    'refersTo': 'VARIABLE_NAME',
                    'description': {
                        'format': 'Object has no fields'
                    }
                }
            }]
        }, self._Pack(self._LocalByName('unused_empty_object')))

  def testWatchedExpressionsSuccess(self):
    unused_dummy_a = 'x'
    unused_dummy_b = {1: 2, 3: 'a'}

    self._collector = CaptureCollectorWithDefaultLocation({
        'id': 'BP_ID',
        'expressions': ['1+2', 'unused_dummy_a*8', 'unused_dummy_b']
    })
    self._collector.Collect(inspect.currentframe())
    self.assertListEqual([{
        'name': '1+2',
        'value': '3',
        'type': 'int'
    }, {
        'name': 'unused_dummy_a*8',
        'value': "'xxxxxxxx'",
        'type': 'str'
    }, {
        'name':
            'unused_dummy_b',
        'type':
            'dict',
        'members': [{
            'name': '1',
            'value': '2',
            'type': 'int'
        }, {
            'name': '3',
            'value': "'a'",
            'type': 'str'
        }]
    }], self._collector.breakpoint['evaluatedExpressions'])

  def testOversizedStringExpression(self):
    # This test checks that string expressions are collected first, up to the
    # max size. The last 18 characters of the string will be missing due to the
    # size for the name (14 bytes), type name (3 bytes), and the opening quote
    # (1 byte). This test may be sensitive to minor changes in the collector
    # code. If it turns out to break easily, consider simply verifying
    # that the first 400 characters are collected, since that should suffice to
    # ensure that we're not using the normal limit of 256 bytes.
    self._collector = CaptureCollectorWithDefaultLocation({
        'id': 'BP_ID',
        'expressions': ['unused_dummy_a']
    })
    self._collector.max_size = 500
    unused_dummy_a = '|'.join(['%04d' % i for i in range(5, 510, 5)])
    self._collector.Collect(inspect.currentframe())
    self.assertListEqual([{
        'name': 'unused_dummy_a',
        'type': 'str',
        'value': "'{0}...".format(unused_dummy_a[0:-18])
    }], self._collector.breakpoint['evaluatedExpressions'])

  def testOversizedListExpression(self):
    self._collector = CaptureCollectorWithDefaultLocation({
        'id': 'BP_ID',
        'expressions': ['unused_dummy_a']
    })
    unused_dummy_a = list(range(0, 100))
    self._collector.Collect(inspect.currentframe())
    # Verify that the list did not get truncated.
    self.assertListEqual([{
        'name':
            'unused_dummy_a',
        'type':
            'list',
        'members': [{
            'type': 'int',
            'value': str(a),
            'name': '[{0}]'.format(a)
        } for a in unused_dummy_a]
    }], self._collector.breakpoint['evaluatedExpressions'])

  def testExpressionNullBytes(self):
    self._collector = CaptureCollectorWithDefaultLocation({
        'id': 'BP_ID',
        'expressions': ['\0']
    })
    self._collector.Collect(inspect.currentframe())

    evaluated_expressions = self._collector.breakpoint['evaluatedExpressions']
    self.assertLen(evaluated_expressions, 1)
    self.assertTrue(evaluated_expressions[0]['status']['isError'])

  def testSyntaxErrorExpression(self):
    self._collector = CaptureCollectorWithDefaultLocation({
        'id': 'BP_ID',
        'expressions': ['2+']
    })
    self._collector.Collect(inspect.currentframe())

    evaluated_expressions = self._collector.breakpoint['evaluatedExpressions']
    self.assertLen(evaluated_expressions, 1)
    self.assertTrue(evaluated_expressions[0]['status']['isError'])
    self.assertEqual('VARIABLE_NAME',
                     evaluated_expressions[0]['status']['refersTo'])

  def testExpressionException(self):
    unused_dummy_a = 1
    unused_dummy_b = 0
    self._collector = CaptureCollectorWithDefaultLocation({
        'id': 'BP_ID',
        'expressions': ['unused_dummy_a/unused_dummy_b']
    })
    self._collector.Collect(inspect.currentframe())

    zero_division_msg = 'division by zero'

    self.assertListEqual([{
        'name': 'unused_dummy_a/unused_dummy_b',
        'status': {
            'isError': True,
            'refersTo': 'VARIABLE_VALUE',
            'description': {
                'format': 'Exception occurred: $0',
                'parameters': [zero_division_msg]
            }
        }
    }], self._collector.breakpoint['evaluatedExpressions'])

  def testMutableExpression(self):

    def ChangeA():
      self._a += 1

    self._a = 0
    ChangeA()
    self._collector = CaptureCollectorWithDefaultLocation({
        'id': 'BP_ID',
        'expressions': ['ChangeA()']
    })
    self._collector.Collect(inspect.currentframe())

    self.assertEqual(1, self._a)
    self.assertListEqual([{
        'name': 'ChangeA()',
        'status': {
            'isError': True,
            'refersTo': 'VARIABLE_VALUE',
            'description': {
                'format':
                    'Exception occurred: $0',
                'parameters': [('Only immutable methods can be '
                                'called from expressions')]
            }
        }
    }], self._collector.breakpoint['evaluatedExpressions'])

  def testPrettyPrinters(self):

    class MyClass(object):
      pass

    def PrettyPrinter1(obj):
      if obj != unused_obj1:
        return None
      return ((('name1_%d' % i, '1_%d' % i) for i in range(2)), 'pp-type1')

    def PrettyPrinter2(obj):
      if obj != unused_obj2:
        return None
      return ((('name2_%d' % i, '2_%d' % i) for i in range(3)), 'pp-type2')

    collector.CaptureCollector.pretty_printers += [
        PrettyPrinter1, PrettyPrinter2
    ]

    unused_obj1 = MyClass()
    unused_obj2 = MyClass()
    unused_obj3 = MyClass()

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    obj_vars = [
        self._Pack(self._LocalByName('unused_obj%d' % i)) for i in range(1, 4)
    ]

    self.assertListEqual([{
        'name':
            'unused_obj1',
        'type':
            'pp-type1',
        'members': [{
            'name': 'name1_0',
            'value': "'1_0'",
            'type': 'str'
        }, {
            'name': 'name1_1',
            'value': "'1_1'",
            'type': 'str'
        }]
    }, {
        'name':
            'unused_obj2',
        'type':
            'pp-type2',
        'members': [{
            'name': 'name2_0',
            'value': "'2_0'",
            'type': 'str'
        }, {
            'name': 'name2_1',
            'value': "'2_1'",
            'type': 'str'
        }, {
            'name': 'name2_2',
            'value': "'2_2'",
            'type': 'str'
        }]
    }, {
        'name':
            'unused_obj3',
        'type':
            __name__ + '.MyClass',
        'members': [{
            'status': {
                'refersTo': 'VARIABLE_NAME',
                'description': {
                    'format': 'Object has no fields'
                }
            }
        }]
    }], obj_vars)

  def testDateTime(self):
    unused_datetime = datetime.datetime(2014, 6, 11, 2, 30)
    unused_date = datetime.datetime(1980, 3, 1)
    unused_time = datetime.time(18, 43, 11)
    unused_timedelta = datetime.timedelta(days=3, microseconds=8237)

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    self.assertDictEqual(
        {
            'name': 'unused_datetime',
            'type': 'datetime.datetime',
            'value': '2014-06-11 02:30:00'
        }, self._Pack(self._LocalByName('unused_datetime')))

    self.assertDictEqual(
        {
            'name': 'unused_date',
            'type': 'datetime.datetime',
            'value': '1980-03-01 00:00:00'
        }, self._Pack(self._LocalByName('unused_date')))

    self.assertDictEqual(
        {
            'name': 'unused_time',
            'type': 'datetime.time',
            'value': '18:43:11'
        }, self._Pack(self._LocalByName('unused_time')))

    self.assertDictEqual(
        {
            'name': 'unused_timedelta',
            'type': 'datetime.timedelta',
            'value': '3 days, 0:00:00.008237'
        }, self._Pack(self._LocalByName('unused_timedelta')))

  def testException(self):
    unused_exception = ValueError('arg1', 2, [3])

    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())
    obj = self._Pack(self._LocalByName('unused_exception'))

    self.assertEqual('unused_exception', obj['name'])
    self.assertEqual('ValueError', obj['type'])
    self.assertListEqual([{
        'value': "'arg1'",
        'type': 'str',
        'name': '[0]'
    }, {
        'value': '2',
        'type': 'int',
        'name': '[1]'
    }, {
        'members': [{
            'value': '3',
            'type': 'int',
            'name': '[0]'
        }],
        'type': 'list',
        'name': '[2]'
    }], obj['members'])

  def testRequestLogIdCapturing(self):
    collector.request_log_id_collector = lambda: 'test_log_id'
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    self.assertIn('labels', self._collector.breakpoint)
    self.assertEqual(
        'test_log_id',
        self._collector.breakpoint['labels'][labels.Breakpoint.REQUEST_LOG_ID])

  def testRequestLogIdCapturingNoId(self):
    collector.request_log_id_collector = lambda: None
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

  def testRequestLogIdCapturingNoCollector(self):
    collector.request_log_id_collector = None
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

  def testUserIdSuccess(self):
    collector.user_id_collector = lambda: ('mdb_user', 'noogler')
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    self.assertIn('evaluatedUserId', self._collector.breakpoint)
    self.assertEqual({
        'kind': 'mdb_user',
        'id': 'noogler'
    }, self._collector.breakpoint['evaluatedUserId'])

  def testUserIdIsNone(self):
    collector.user_id_collector = lambda: (None, None)
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    self.assertNotIn('evaluatedUserId', self._collector.breakpoint)

  def testUserIdNoKind(self):
    collector.user_id_collector = lambda: (None, 'noogler')
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    self.assertNotIn('evaluatedUserId', self._collector.breakpoint)

  def testUserIdNoValue(self):
    collector.user_id_collector = lambda: ('mdb_user', None)
    self._collector = CaptureCollectorWithDefaultLocation({'id': 'BP_ID'})
    self._collector.Collect(inspect.currentframe())

    self.assertNotIn('evaluatedUserId', self._collector.breakpoint)

  def _LocalByName(self, name, frame=0):
    for local in self._collector.breakpoint['stackFrames'][frame]['locals']:
      if local['name'] == name:
        return local
    self.fail('Local %s not found in frame %d' % (name, frame))

  def _Pack(self, variable):
    """Embeds variables referenced through var_index."""
    packed_variable = copy.copy(variable)

    var_index = variable.get('varTableIndex')
    if var_index is not None:
      packed_variable.update(
          self._collector.breakpoint['variableTable'][var_index])
      del packed_variable['varTableIndex']

    if 'members' in packed_variable:
      packed_variable['members'] = [
          self._Pack(member) for member in packed_variable['members']
      ]

    return packed_variable


class LogCollectorTest(absltest.TestCase):
  """Unit test for log collector."""

  def setUp(self):
    self._logger = logging.getLogger('test')

    class LogVerifier(logging.Handler):

      def __init__(self):
        super(LogVerifier, self).__init__()
        self._received_records = []

      def emit(self, record):
        self._received_records.append(record)

      def GotMessage(self,
                     msg,
                     level=logging.INFO,
                     line_number=10,
                     func_name=None):
        """Checks that the given message was logged correctly.

        This method verifies both the contents and the source location of the
        message match expectations.

        Args:
          msg: The expected message
          level: The expected logging level.
          line_number: The expected line number.
          func_name: If specified, the expected log record must have a funcName
            equal to this value.
        Returns:
          True iff the oldest unverified message matches the given attributes.
        """
        record = self._received_records.pop(0)
        frame = inspect.currentframe().f_back
        if level != record.levelno:
          logging.error('Expected log level %d, got %d (%s)', level,
                        record.levelno, record.levelname)
          return False
        if msg != record.msg:
          logging.error('Expected msg "%s", received "%s"', msg, record.msg)
          return False
        pathname = collector.NormalizePath(frame.f_code.co_filename)
        if pathname != record.pathname:
          logging.error('Expected pathname "%s", received "%s"', pathname,
                        record.pathname)
          return False
        if os.path.basename(pathname) != record.filename:
          logging.error('Expected filename "%s", received "%s"',
                        os.path.basename(pathname), record.filename)
          return False
        if func_name and func_name != record.funcName:
          logging.error('Expected function "%s", received "%s"', func_name,
                        record.funcName)
          return False
        if line_number and record.lineno != line_number:
          logging.error('Expected lineno %d, received %d', line_number,
                        record.lineno)
          return False
        for attr in ['cdbg_pathname', 'cdbg_lineno']:
          if hasattr(record, attr):
            logging.error('Attribute %s still present in log record', attr)
            return False
        return True

      def CheckMessageSafe(self, msg):
        """Checks that the given message was logged correctly.

        Unlike GotMessage, this will only check the contents, and will not log
        an error or pop the record if the message does not match.

        Args:
          msg: The expected message
        Returns:
          True iff the oldest unverified message matches the given attributes.
        """
        record = self._received_records[0]
        if msg != record.msg:
          print(record.msg)
          return False
        self._received_records.pop(0)
        return True

    self._verifier = LogVerifier()
    self._logger.addHandler(self._verifier)
    self._logger.setLevel(logging.INFO)
    collector.SetLogger(self._logger)

    # Give some time for the global quota to recover
    time.sleep(0.1)

  def tearDown(self):
    self._logger.removeHandler(self._verifier)

  def ResetGlobalLogQuota(self):
    # The global log quota takes up to 5 seconds to fully fill back up to
    # capacity (kDynamicLogCapacityFactor is 5). The capacity is 5 times the per
    # second fill rate. The best we can do is a sleep, since the global
    # leaky_bucket instance is inaccessible to the test.
    time.sleep(5.0)

  def ResetGlobalLogBytesQuota(self):
    # The global log bytes quota takes up to 2 seconds to fully fill back up to
    # capacity (kDynamicLogBytesCapacityFactor is 2). The capacity is twice the
    # per second fill rate. The best we can do is a sleep, since the global
    # leaky_bucket instance is inaccessible to the test.
    time.sleep(2.0)

  def testLogQuota(self):
    # Attempt to get to a known starting state by letting the global quota fully
    # recover so the ordering of tests ideally doesn't affect this test.
    self.ResetGlobalLogQuota()
    bucket_max_capacity = 250
    collector = LogCollectorWithDefaultLocation({
        'logMessageFormat': '$0',
        'expressions': ['i']
    })
    for i in range(0, bucket_max_capacity * 2):
      self.assertIsNone(collector.Log(inspect.currentframe()))
      if not self._verifier.CheckMessageSafe('LOGPOINT: %s' % i):
        self.assertGreaterEqual(i, bucket_max_capacity,
                                'Log quota exhausted earlier than expected')
        self.assertTrue(
            self._verifier.CheckMessageSafe(LOGPOINT_PAUSE_MSG),
            'Quota hit message not logged')
        time.sleep(0.6)
        self.assertIsNone(collector.Log(inspect.currentframe()))
        self.assertTrue(
            self._verifier.CheckMessageSafe('LOGPOINT: %s' % i),
            'Logging not resumed after quota recovery time')
        return
    self.fail('Logging was never paused when quota was exceeded')

  def testLogBytesQuota(self):
    # Attempt to get to a known starting state by letting the global quota fully
    # recover so the ordering of tests ideally doesn't affect this test.
    self.ResetGlobalLogBytesQuota()

    # Default capacity is 40960, though based on how the leaky bucket is
    # implemented, it can allow effectively twice that amount to go out in a
    # very short time frame. So the third 30k message should pause.
    msg = ' ' * 30000
    collector = LogCollectorWithDefaultLocation({'logMessageFormat': msg})
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage('LOGPOINT: ' + msg))
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage('LOGPOINT: ' + msg))
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.CheckMessageSafe(LOGPOINT_PAUSE_MSG),
        'Quota hit message not logged')
    time.sleep(0.6)
    collector._definition['logMessageFormat'] = 'hello'
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage('LOGPOINT: hello'),
        'Logging was not resumed after quota recovery time')

  def testMissingLogLevel(self):
    # Missing is equivalent to INFO.
    collector = LogCollectorWithDefaultLocation({'logMessageFormat': 'hello'})
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage('LOGPOINT: hello'))

  def testUndefinedLogLevel(self):
    collector.log_info_message = None
    collector = LogCollectorWithDefaultLocation({'logLevel': 'INFO'})
    self.assertDictEqual(
        {
            'isError': True,
            'description': {
                'format': 'Log action on a breakpoint not supported'
            }
        }, collector.Log(inspect.currentframe()))

  def testLogInfo(self):
    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': 'hello'
    })
    collector._definition['location']['line'] = 20
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage(
            'LOGPOINT: hello',
            func_name='LogCollectorTest.testLogInfo',
            line_number=20))

  def testLogWarning(self):
    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'WARNING',
        'logMessageFormat': 'hello'
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage(
            'LOGPOINT: hello',
            level=logging.WARNING,
            func_name='LogCollectorTest.testLogWarning'))

  def testLogError(self):
    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'ERROR',
        'logMessageFormat': 'hello'
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage(
            'LOGPOINT: hello',
            level=logging.ERROR,
            func_name='LogCollectorTest.testLogError'))

  def testBadExpression(self):
    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': 'a=$0, b=$1',
        'expressions': ['-', '+']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage(
            'LOGPOINT: a=<Expression could not be compiled: unexpected EOF while '
            'parsing>, b=<Expression could not be compiled: unexpected EOF while '
            'parsing>'))

  def testDollarEscape(self):
    unused_integer = 12345

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$ $$ $$$ $$$$ $0 $$0 $$$0 $$$$0 $1 hello',
        'expressions': ['unused_integer']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    msg = 'LOGPOINT: $ $ $$ $$ 12345 $0 $12345 $$0 <N/A> hello'
    self.assertTrue(self._verifier.GotMessage(msg))

  def testInvalidExpressionIndex(self):
    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': 'a=$0'
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage('LOGPOINT: a=<N/A>'))

  def testException(self):
    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['[][1]']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage(
            'LOGPOINT: <Exception occurred: list index out of range>'))

  def testMutableExpression(self):

    def MutableMethod():  # pylint: disable=unused-variable
      self.abc = None

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['MutableMethod()']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage(
            'LOGPOINT: <Exception occurred: Only immutable methods can be called '
            'from expressions>'))

  def testNone(self):
    unused_none = None

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_none']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage('LOGPOINT: None'))

  def testPrimitives(self):
    unused_boolean = True
    unused_integer = 12345
    unused_string = 'hello'

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0,$1,$2',
        'expressions': ['unused_boolean', 'unused_integer', 'unused_string']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage("LOGPOINT: True,12345,'hello'"))

  def testLongString(self):
    unused_string = '1234567890'

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_string']
    })
    collector.max_value_len = 9
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage("LOGPOINT: '123456789..."))

  def testLongBytes(self):
    unused_bytes = bytearray([i for i in range(20)])

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_bytes']
    })
    collector.max_value_len = 20
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage(r"LOGPOINT: bytearray(b'\x00\x01\..."))

  def testDate(self):
    unused_datetime = datetime.datetime(2014, 6, 11, 2, 30)
    unused_date = datetime.datetime(1980, 3, 1)
    unused_time = datetime.time(18, 43, 11)
    unused_timedelta = datetime.timedelta(days=3, microseconds=8237)

    collector = LogCollectorWithDefaultLocation({
        'logLevel':
            'INFO',
        'logMessageFormat':
            '$0;$1;$2;$3',
        'expressions': [
            'unused_datetime', 'unused_date', 'unused_time', 'unused_timedelta'
        ]
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage(
            'LOGPOINT: 2014-06-11 02:30:00;1980-03-01 00:00:00;'
            '18:43:11;3 days, 0:00:00.008237'))

  def testSet(self):
    unused_set = set(['a'])

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_set']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage("LOGPOINT: {'a'}"))

  def testTuple(self):
    unused_tuple = (1, 2, 3, 4, 5)

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_tuple']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage('LOGPOINT: (1, 2, 3, 4, 5)'))

  def testList(self):
    unused_list = ['a', 'b', 'c']

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_list']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage("LOGPOINT: ['a', 'b', 'c']"))

  def testOversizedList(self):
    unused_list = [1, 2, 3, 4]

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_list']
    })
    collector.max_list_items = 3
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage('LOGPOINT: [1, 2, 3, ...]'))

  def testSlice(self):
    unused_slice = slice(1, 10)

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_slice']
    })
    collector.max_list_items = 3
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage('LOGPOINT: slice(1, 10, None)'))

  def testMap(self):
    unused_map = {'a': 1}

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_map']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage("LOGPOINT: {'a': 1}"))

  def testObject(self):

    class MyClass(object):

      def __init__(self):
        self.some = 'thing'

    unused_my = MyClass()

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_my']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(self._verifier.GotMessage("LOGPOINT: {'some': 'thing'}"))

  def testNestedBelowLimit(self):
    unused_list = [1, [2], [1, 2, 3], [1, [1, 2, 3]], 5]

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_list']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage(
            'LOGPOINT: [1, [2], [1, 2, 3], [1, [1, 2, 3]], 5]'))

  def testNestedAtLimits(self):
    unused_list = [
        1, [1, 2, 3, 4, 5], [[1, 2, 3, 4, 5], 2, 3, 4, 5], 4, 5, 6, 7, 8, 9
    ]

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_list']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage(
            'LOGPOINT: [1, [1, 2, 3, 4, 5], [[1, 2, 3, 4, 5], 2, 3, 4, 5], '
            '4, 5, 6, 7, 8, 9]'))

  def testNestedRecursionLimit(self):
    unused_list = [1, [[2, [3]], 4], 5]

    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_list']
    })
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage('LOGPOINT: [1, [[2, %s], 4], 5]' % type([])))

  def testNestedRecursionItemLimits(self):
    unused_list = [1, [1, [1, [2], 3, 4], 3, 4], 3, 4]

    list_type = "<class 'list'>"
    collector = LogCollectorWithDefaultLocation({
        'logLevel': 'INFO',
        'logMessageFormat': '$0',
        'expressions': ['unused_list']
    })
    collector.max_list_items = 3
    collector.max_sublist_items = 3
    self.assertIsNone(collector.Log(inspect.currentframe()))
    self.assertTrue(
        self._verifier.GotMessage(
            'LOGPOINT: [1, [1, [1, %s, 3, ...], 3, ...], 3, ...]' % list_type))

  def testDetermineType(self):
    builtin_prefix = 'builtins.'
    path_prefix = 'googleclouddebugger.collector.'
    test_data = (
        (builtin_prefix + 'int', 5),
        (builtin_prefix + 'str', 'hello'),
        (builtin_prefix + 'function', collector.DetermineType),
        (path_prefix + 'LineNoFilter', collector.LineNoFilter()),
    )

    for type_string, value in test_data:
      self.assertEqual(type_string, collector.DetermineType(value))


if __name__ == '__main__':
  absltest.main()
