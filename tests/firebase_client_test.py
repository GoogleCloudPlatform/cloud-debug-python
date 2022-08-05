"""Unit tests for firebase_client module."""

import errno
import os
import socket
import sys
import tempfile
import time
from unittest import mock
from unittest.mock import MagicMock
from unittest.mock import call
from unittest.mock import patch
import requests
import requests_mock

from googleapiclient.errors import HttpError
from googleclouddebugger import version
from googleclouddebugger import firebase_client

from absl.testing import absltest
from absl.testing import parameterized

import firebase_admin.credentials
from firebase_admin.exceptions import FirebaseError

TEST_PROJECT_ID = 'test-project-id'
METADATA_PROJECT_URL = ('http://metadata.google.internal/computeMetadata/'
                        'v1/project/project-id')


class FakeEvent:

  def __init__(self, event_type, path, data):
    self.event_type = event_type
    self.path = path
    self.data = data


class FakeReference:

  def __init__(self):
    self.subscriber = None

  def listen(self, callback):
    self.subscriber = callback

  def update(self, event_type, path, data):
    if self.subscriber:
      event = FakeEvent(event_type, path, data)
      self.subscriber(event)


class FirebaseClientTest(parameterized.TestCase):
  """Simulates service account authentication."""

  def setUp(self):
    version.__version__ = 'test'

    self._client = firebase_client.FirebaseClient()

    self.breakpoints_changed_count = 0
    self.breakpoints = {}

    # Speed up the delays for retry loops.
    for backoff in [
        self._client.register_backoff, self._client.subscribe_backoff,
        self._client.update_backoff
    ]:
      backoff.min_interval_sec /= 100000.0
      backoff.max_interval_sec /= 100000.0
      backoff._current_interval_sec /= 100000.0

    # Set up patchers.
    patcher = patch('firebase_admin.initialize_app')
    self._mock_initialize_app = patcher.start()
    self.addCleanup(patcher.stop)

    patcher = patch('firebase_admin.db.reference')
    self._mock_db_ref = patcher.start()
    self.addCleanup(patcher.stop)

    # Set up the mocks for the database refs.
    self._mock_register_ref = MagicMock()
    self._fake_subscribe_ref = FakeReference()
    self._mock_db_ref.side_effect = [
        self._mock_register_ref, self._fake_subscribe_ref
    ]

  def tearDown(self):
    self._client.Stop()

  def testSetupAuthDefault(self):
    # By default, we try getting the project id from the metadata server.
    # Note that actual credentials are not fetched.
    with requests_mock.Mocker() as m:
      m.get(METADATA_PROJECT_URL, text=TEST_PROJECT_ID)

      self._client.SetupAuth()

    self.assertEqual(TEST_PROJECT_ID, self._client._project_id)
    self.assertEqual(f'https://{TEST_PROJECT_ID}-cdbg.firebaseio.com',
                     self._client._database_url)

  def testSetupAuthOverrideProjectIdNumber(self):
    # If a project id is provided, we use it.
    project_id = 'project2'
    self._client.SetupAuth(project_id=project_id)

    self.assertEqual(project_id, self._client._project_id)
    self.assertEqual(f'https://{project_id}-cdbg.firebaseio.com',
                     self._client._database_url)

  def testSetupAuthServiceAccountJsonAuth(self):
    # We'll load credentials from the provided file (mocked for simplicity)
    with mock.patch.object(firebase_admin.credentials,
                           'Certificate') as firebase_certificate:
      json_file = tempfile.NamedTemporaryFile()
      # And load the project id from the file as well.
      with open(json_file.name, 'w', encoding='utf-8') as f:
        f.write(f'{{"project_id": "{TEST_PROJECT_ID}"}}')
      self._client.SetupAuth(service_account_json_file=json_file.name)

    firebase_certificate.assert_called_with(json_file.name)
    self.assertEqual(TEST_PROJECT_ID, self._client._project_id)

  def testSetupAuthNoProjectId(self):
    # There will be an exception raised if we try to contact the metadata
    # server on a non-gcp machine.
    with requests_mock.Mocker() as m:
      m.get(METADATA_PROJECT_URL, exc=requests.exceptions.RequestException)

      with self.assertRaises(firebase_client.NoProjectIdError):
        self._client.SetupAuth()

  def testStart(self):
    self._client.SetupAuth(project_id=TEST_PROJECT_ID)
    self._client.Start()
    self._client.subscription_complete.wait()

    debuggee_id = self._client._debuggee_id

    self._mock_initialize_app.assert_called_with(
        None, {'databaseURL': f'https://{TEST_PROJECT_ID}-cdbg.firebaseio.com'})
    self.assertEqual([
        call(f'cdbg/debuggees/{debuggee_id}'),
        call(f'cdbg/breakpoints/{debuggee_id}/active')
    ], self._mock_db_ref.call_args_list)

  def testStartRegisterRetry(self):
    # A new db ref is fetched on each retry.
    self._mock_db_ref.side_effect = [
        self._mock_register_ref, self._mock_register_ref,
        self._fake_subscribe_ref
    ]

    # Fail once, then succeed on retry.
    self._mock_register_ref.set.side_effect = [FirebaseError(1, 'foo'), None]

    self._client.SetupAuth(project_id=TEST_PROJECT_ID)
    self._client.Start()
    self._client.registration_complete.wait()

    self.assertEqual(2, self._mock_register_ref.set.call_count)

  def testStartSubscribeRetry(self):
    mock_subscribe_ref = MagicMock()
    mock_subscribe_ref.listen.side_effect = FirebaseError(1, 'foo')

    # A new db ref is fetched on each retry.
    self._mock_db_ref.side_effect = [
        self._mock_register_ref,
        mock_subscribe_ref,  # Fail the first time
        self._fake_subscribe_ref  # Succeed the second time
    ]

    self._client.SetupAuth(project_id=TEST_PROJECT_ID)
    self._client.Start()
    self._client.subscription_complete.wait()

    self.assertEqual(3, self._mock_db_ref.call_count)

  def testBreakpointSubscription(self):
    # This class will keep track of the breakpoint updates and will check
    # them against expectations.
    class ResultChecker:

      def __init__(self, expected_results, test):
        self._expected_results = expected_results
        self._test = test
        self._change_count = 0

      def callback(self, new_breakpoints):
        self._test.assertEqual(self._expected_results[self._change_count],
                               new_breakpoints)
        self._change_count += 1

    breakpoints = [
        {
            'id': 'breakpoint-0',
            'location': {
                'path': 'foo.py',
                'line': 18
            }
        },
        {
            'id': 'breakpoint-1',
            'location': {
                'path': 'bar.py',
                'line': 23
            }
        },
        {
            'id': 'breakpoint-2',
            'location': {
                'path': 'baz.py',
                'line': 45
            }
        },
    ]

    expected_results = [[breakpoints[0]], [breakpoints[0], breakpoints[1]],
                        [breakpoints[0], breakpoints[1], breakpoints[2]],
                        [breakpoints[1], breakpoints[2]]]
    result_checker = ResultChecker(expected_results, self)

    self._client.on_active_breakpoints_changed = result_checker.callback

    self._client.SetupAuth(project_id=TEST_PROJECT_ID)
    self._client.Start()
    self._client.subscription_complete.wait()

    # Send in updates to trigger the subscription callback.
    self._fake_subscribe_ref.update('put', '/',
                                    {breakpoints[0]['id']: breakpoints[0]})
    self._fake_subscribe_ref.update('patch', '/',
                                    {breakpoints[1]['id']: breakpoints[1]})
    self._fake_subscribe_ref.update('put', f'/{breakpoints[2]["id"]}',
                                    breakpoints[2])
    self._fake_subscribe_ref.update('put', f'/{breakpoints[0]["id"]}', None)

    self.assertEqual(len(expected_results), result_checker._change_count)

  def testEnqueueBreakpointUpdate(self):
    active_ref_mock = MagicMock()
    snapshot_ref_mock = MagicMock()
    final_ref_mock = MagicMock()

    self._mock_db_ref.side_effect = [
        self._mock_register_ref, self._fake_subscribe_ref, active_ref_mock,
        snapshot_ref_mock, final_ref_mock
    ]

    self._client.SetupAuth(project_id=TEST_PROJECT_ID)
    self._client.Start()
    self._client.subscription_complete.wait()

    debuggee_id = self._client._debuggee_id
    breakpoint_id = 'breakpoint-0'

    input_breakpoint = {
        'id': breakpoint_id,
        'location': {
            'path': 'foo.py',
            'line': 18
        },
        'isFinalState': True,
        'evaluatedExpressions': ['expressions go here'],
        'stackFrames': ['stuff goes here'],
        'variableTable': ['lots', 'of', 'variables'],
    }
    short_breakpoint = {
        'id': breakpoint_id,
        'location': {
            'path': 'foo.py',
            'line': 18
        },
        'isFinalState': True,
        'action': 'CAPTURE',
        'finalTimeUnixMsec': {
            '.sv': 'timestamp'
        }
    }
    full_breakpoint = {
        'id': breakpoint_id,
        'location': {
            'path': 'foo.py',
            'line': 18
        },
        'isFinalState': True,
        'action': 'CAPTURE',
        'evaluatedExpressions': ['expressions go here'],
        'stackFrames': ['stuff goes here'],
        'variableTable': ['lots', 'of', 'variables'],
        'finalTimeUnixMsec': {
            '.sv': 'timestamp'
        }
    }

    self._client.EnqueueBreakpointUpdate(input_breakpoint)

    # Wait for the breakpoint to be sent.
    while self._client._transmission_queue:
      time.sleep(0.1)

    db_ref_calls = self._mock_db_ref.call_args_list
    self.assertEqual(
        call(f'cdbg/breakpoints/{debuggee_id}/active/{breakpoint_id}'),
        db_ref_calls[2])
    self.assertEqual(
        call(f'cdbg/breakpoints/{debuggee_id}/snapshots/{breakpoint_id}'),
        db_ref_calls[3])
    self.assertEqual(
        call(f'cdbg/breakpoints/{debuggee_id}/final/{breakpoint_id}'),
        db_ref_calls[4])

    active_ref_mock.delete.assert_called_once()
    snapshot_ref_mock.set.assert_called_once_with(full_breakpoint)
    final_ref_mock.set.assert_called_once_with(short_breakpoint)

  def testEnqueueBreakpointUpdateWithFailedLogpoint(self):
    active_ref_mock = MagicMock()
    snapshot_ref_mock = MagicMock()
    final_ref_mock = MagicMock()

    self._mock_db_ref.side_effect = [
        self._mock_register_ref, self._fake_subscribe_ref, active_ref_mock,
        final_ref_mock
    ]

    self._client.SetupAuth(project_id=TEST_PROJECT_ID)
    self._client.Start()
    self._client.subscription_complete.wait()

    debuggee_id = self._client._debuggee_id
    breakpoint_id = 'logpoint-0'

    input_breakpoint = {
        'id': breakpoint_id,
        'location': {
            'path': 'foo.py',
            'line': 18
        },
        'action': 'LOG',
        'isFinalState': True,
        'status': {
            'isError': True,
            'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
        },
    }
    output_breakpoint = {
        'id': breakpoint_id,
        'location': {
            'path': 'foo.py',
            'line': 18
        },
        'isFinalState': True,
        'action': 'LOG',
        'status': {
            'isError': True,
            'refersTo': 'BREAKPOINT_SOURCE_LOCATION',
        },
        'finalTimeUnixMsec': {
            '.sv': 'timestamp'
        }
    }

    self._client.EnqueueBreakpointUpdate(input_breakpoint)

    # Wait for the breakpoint to be sent.
    while self._client._transmission_queue:
      time.sleep(0.1)

    db_ref_calls = self._mock_db_ref.call_args_list
    self.assertEqual(
        call(f'cdbg/breakpoints/{debuggee_id}/active/{breakpoint_id}'),
        db_ref_calls[2])
    self.assertEqual(
        call(f'cdbg/breakpoints/{debuggee_id}/final/{breakpoint_id}'),
        db_ref_calls[3])

    active_ref_mock.delete.assert_called_once()
    final_ref_mock.set.assert_called_once_with(output_breakpoint)

  def testEnqueueBreakpointUpdateRetry(self):
    active_ref_mock = MagicMock()
    snapshot_ref_mock = MagicMock()
    final_ref_mock = MagicMock()

    # This test will have multiple failures, one for each of the firebase writes.
    # UNAVAILABLE errors are retryable.
    active_ref_mock.delete.side_effect = [
        FirebaseError('UNAVAILABLE', 'active error'), None, None, None
    ]
    snapshot_ref_mock.set.side_effect = [
        FirebaseError('UNAVAILABLE', 'snapshot error'), None, None
    ]
    final_ref_mock.set.side_effect = [
        FirebaseError('UNAVAILABLE', 'final error'), None
    ]

    self._mock_db_ref.side_effect = [
        self._mock_register_ref,
        self._fake_subscribe_ref,  # setup
        active_ref_mock,  # attempt 1
        active_ref_mock,
        snapshot_ref_mock,  # attempt 2
        active_ref_mock,
        snapshot_ref_mock,
        final_ref_mock,  # attempt 3
        active_ref_mock,
        snapshot_ref_mock,
        final_ref_mock  # attempt 4
    ]

    self._client.SetupAuth(project_id=TEST_PROJECT_ID)
    self._client.Start()
    self._client.subscription_complete.wait()

    debuggee_id = self._client._debuggee_id
    breakpoint_id = 'breakpoint-0'

    input_breakpoint = {
        'id': breakpoint_id,
        'location': {
            'path': 'foo.py',
            'line': 18
        },
        'isFinalState': True,
        'evaluatedExpressions': ['expressions go here'],
        'stackFrames': ['stuff goes here'],
        'variableTable': ['lots', 'of', 'variables'],
    }
    short_breakpoint = {
        'id': breakpoint_id,
        'location': {
            'path': 'foo.py',
            'line': 18
        },
        'isFinalState': True,
        'action': 'CAPTURE',
        'finalTimeUnixMsec': {
            '.sv': 'timestamp'
        }
    }
    full_breakpoint = {
        'id': breakpoint_id,
        'location': {
            'path': 'foo.py',
            'line': 18
        },
        'isFinalState': True,
        'action': 'CAPTURE',
        'evaluatedExpressions': ['expressions go here'],
        'stackFrames': ['stuff goes here'],
        'variableTable': ['lots', 'of', 'variables'],
        'finalTimeUnixMsec': {
            '.sv': 'timestamp'
        }
    }

    self._client.EnqueueBreakpointUpdate(input_breakpoint)

    # Wait for the breakpoint to be sent.  Retries will have occured.
    while self._client._transmission_queue:
      time.sleep(0.1)

    active_ref_mock.delete.assert_has_calls([call()] * 4)
    snapshot_ref_mock.set.assert_has_calls([call(full_breakpoint)] * 3)
    final_ref_mock.set.assert_has_calls([call(short_breakpoint)] * 2)

  def _TestInitializeLabels(self, module_var, version_var, minor_var):
    self._client.SetupAuth(project_id=TEST_PROJECT_ID)

    self._client.InitializeDebuggeeLabels({
        'module': 'my_module',
        'version': '1',
        'minorversion': '23',
        'something_else': 'irrelevant'
    })
    self.assertEqual(
        {
            'projectid': 'test-project-id',
            'module': 'my_module',
            'version': '1',
            'minorversion': '23',
            'platform': 'default'
        }, self._client._debuggee_labels)
    self.assertEqual('test-project-id-my_module-1',
                     self._client._GetDebuggeeDescription())

    uniquifier1 = self._client._ComputeUniquifier(
        {'labels': self._client._debuggee_labels})
    self.assertTrue(uniquifier1)  # Not empty string.

    try:
      os.environ[module_var] = 'env_module'
      os.environ[version_var] = '213'
      os.environ[minor_var] = '3476734'
      self._client.InitializeDebuggeeLabels(None)
      self.assertEqual(
          {
              'projectid': 'test-project-id',
              'module': 'env_module',
              'version': '213',
              'minorversion': '3476734',
              'platform': 'default'
          }, self._client._debuggee_labels)
      self.assertEqual('test-project-id-env_module-213',
                       self._client._GetDebuggeeDescription())

      os.environ[module_var] = 'default'
      os.environ[version_var] = '213'
      os.environ[minor_var] = '3476734'
      self._client.InitializeDebuggeeLabels({'minorversion': 'something else'})
      self.assertEqual(
          {
              'projectid': 'test-project-id',
              'version': '213',
              'minorversion': 'something else',
              'platform': 'default'
          }, self._client._debuggee_labels)
      self.assertEqual('test-project-id-213',
                       self._client._GetDebuggeeDescription())

    finally:
      del os.environ[module_var]
      del os.environ[version_var]
      del os.environ[minor_var]

  def testInitializeLegacyDebuggeeLabels(self):
    self._TestInitializeLabels('GAE_MODULE_NAME', 'GAE_MODULE_VERSION',
                               'GAE_MINOR_VERSION')

  def testInitializeDebuggeeLabels(self):
    self._TestInitializeLabels('GAE_SERVICE', 'GAE_VERSION',
                               'GAE_DEPLOYMENT_ID')

  def testInitializeCloudRunDebuggeeLabels(self):
    self._client.SetupAuth(project_id=TEST_PROJECT_ID)

    try:
      os.environ['K_SERVICE'] = 'env_module'
      os.environ['K_REVISION'] = '213'
      self._client.InitializeDebuggeeLabels(None)
      self.assertEqual(
          {
              'projectid': 'test-project-id',
              'module': 'env_module',
              'version': '213',
              'platform': 'default'
          }, self._client._debuggee_labels)
      self.assertEqual('test-project-id-env_module-213',
                       self._client._GetDebuggeeDescription())

    finally:
      del os.environ['K_SERVICE']
      del os.environ['K_REVISION']

  def testInitializeCloudFunctionDebuggeeLabels(self):
    self._client.SetupAuth(project_id=TEST_PROJECT_ID)

    try:
      os.environ['FUNCTION_NAME'] = 'fcn-name'
      os.environ['X_GOOGLE_FUNCTION_VERSION'] = '213'
      self._client.InitializeDebuggeeLabels(None)
      self.assertEqual(
          {
              'projectid': 'test-project-id',
              'module': 'fcn-name',
              'version': '213',
              'platform': 'cloud_function'
          }, self._client._debuggee_labels)
      self.assertEqual('test-project-id-fcn-name-213',
                       self._client._GetDebuggeeDescription())

    finally:
      del os.environ['FUNCTION_NAME']
      del os.environ['X_GOOGLE_FUNCTION_VERSION']

  def testInitializeCloudFunctionUnversionedDebuggeeLabels(self):
    self._client.SetupAuth(project_id=TEST_PROJECT_ID)

    try:
      os.environ['FUNCTION_NAME'] = 'fcn-name'
      self._client.InitializeDebuggeeLabels(None)
      self.assertEqual(
          {
              'projectid': 'test-project-id',
              'module': 'fcn-name',
              'version': 'unversioned',
              'platform': 'cloud_function'
          }, self._client._debuggee_labels)
      self.assertEqual('test-project-id-fcn-name-unversioned',
                       self._client._GetDebuggeeDescription())

    finally:
      del os.environ['FUNCTION_NAME']

  def testInitializeCloudFunctionWithRegionDebuggeeLabels(self):
    self._client.SetupAuth(project_id=TEST_PROJECT_ID)

    try:
      os.environ['FUNCTION_NAME'] = 'fcn-name'
      os.environ['FUNCTION_REGION'] = 'fcn-region'
      self._client.InitializeDebuggeeLabels(None)
      self.assertEqual(
          {
              'projectid': 'test-project-id',
              'module': 'fcn-name',
              'version': 'unversioned',
              'platform': 'cloud_function',
              'region': 'fcn-region'
          }, self._client._debuggee_labels)
      self.assertEqual('test-project-id-fcn-name-unversioned',
                       self._client._GetDebuggeeDescription())

    finally:
      del os.environ['FUNCTION_NAME']
      del os.environ['FUNCTION_REGION']

  def testAppFilesUniquifierNoMinorVersion(self):
    """Verify that uniquifier_computer is used if minor version not defined."""
    self._client.SetupAuth(project_id=TEST_PROJECT_ID)

    root = tempfile.mkdtemp('', 'fake_app_')
    sys.path.insert(0, root)
    try:
      uniquifier1 = self._client._ComputeUniquifier({})

      with open(os.path.join(root, 'app.py'), 'w', encoding='utf-8') as f:
        f.write('hello')
      uniquifier2 = self._client._ComputeUniquifier({})
    finally:
      del sys.path[0]

    self.assertNotEqual(uniquifier1, uniquifier2)

  def testAppFilesUniquifierWithMinorVersion(self):
    """Verify that uniquifier_computer not used if minor version is defined."""
    self._client.SetupAuth(project_id=TEST_PROJECT_ID)

    root = tempfile.mkdtemp('', 'fake_app_')

    os.environ['GAE_MINOR_VERSION'] = '12345'
    sys.path.insert(0, root)
    try:
      self._client.InitializeDebuggeeLabels(None)

      uniquifier1 = self._client._GetDebuggee()['uniquifier']

      with open(os.path.join(root, 'app.py'), 'w', encoding='utf-8') as f:
        f.write('hello')
      uniquifier2 = self._client._GetDebuggee()['uniquifier']
    finally:
      del os.environ['GAE_MINOR_VERSION']
      del sys.path[0]

    self.assertEqual(uniquifier1, uniquifier2)

  def testSourceContext(self):
    self._client.SetupAuth(project_id=TEST_PROJECT_ID)

    root = tempfile.mkdtemp('', 'fake_app_')
    source_context_path = os.path.join(root, 'source-context.json')

    sys.path.insert(0, root)
    try:
      debuggee_no_source_context1 = self._client._GetDebuggee()

      with open(source_context_path, 'w', encoding='utf-8') as f:
        f.write('not a valid JSON')
      debuggee_bad_source_context = self._client._GetDebuggee()

      with open(os.path.join(root, 'fake_app.py'), 'w', encoding='utf-8') as f:
        f.write('pretend')
      debuggee_no_source_context2 = self._client._GetDebuggee()

      with open(source_context_path, 'w', encoding='utf-8') as f:
        f.write('{"what": "source context"}')
      debuggee_with_source_context = self._client._GetDebuggee()

      os.remove(source_context_path)
    finally:
      del sys.path[0]

    self.assertNotIn('sourceContexts', debuggee_no_source_context1)
    self.assertNotIn('sourceContexts', debuggee_bad_source_context)
    self.assertListEqual([{
        'what': 'source context'
    }], debuggee_with_source_context['sourceContexts'])

    uniquifiers = set()
    uniquifiers.add(debuggee_no_source_context1['uniquifier'])
    uniquifiers.add(debuggee_with_source_context['uniquifier'])
    uniquifiers.add(debuggee_bad_source_context['uniquifier'])
    self.assertLen(uniquifiers, 1)
    uniquifiers.add(debuggee_no_source_context2['uniquifier'])
    self.assertLen(uniquifiers, 2)


if __name__ == '__main__':
  absltest.main()
