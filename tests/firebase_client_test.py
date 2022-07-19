"""Unit tests for firebase_client module."""

import datetime
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

import google.auth
from google.oauth2 import service_account
from absl.testing import absltest
from absl.testing import parameterized

from googleclouddebugger import firebase_client

import firebase_admin.credentials

TEST_DEBUGGEE_ID = 'gcp:debuggee-id'
TEST_PROJECT_ID = 'test-project-id'
TEST_PROJECT_NUMBER = '123456789'
TEST_SERVICE_ACCOUNT_EMAIL = 'a@developer.gserviceaccount.com'
METADATA_PROJECT_URL = 'http://metadata.google.internal/computeMetadata/v1/project/project-id'


class HttpResponse(object):

  def __init__(self, status):
    self.status = status
    self.reason = None


def HttpErrorTimeout():
  return HttpError(HttpResponse(408), b'Fake timeout')


def HttpConnectionReset():
  return socket.error(errno.ECONNRESET, 'Fake connection reset')


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
    #self._client.on_active_breakpoints_changed = self._BreakpointsChanged


#    patcher = mock.patch.object(google.auth, 'default')
#    self._default_auth_mock = patcher.start()
#    self._default_auth_mock.return_value = (None, TEST_PROJECT_ID)
#    self.addCleanup(patcher.stop)

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
      with open(json_file.name, 'w') as f:
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

  @patch('firebase_admin.db.reference')
  @patch('firebase_admin.initialize_app')
  def testStart(self, mock_initialize_app, mock_db_ref):
    self._client.SetupAuth(project_id=TEST_PROJECT_ID)
    self._client.Start()
    self._client.subscription_complete.wait()

    debuggee_id = self._client._debuggee_id

    mock_initialize_app.assert_called_with(
        None, {'databaseURL': f'https://{TEST_PROJECT_ID}-cdbg.firebaseio.com'})
    self.assertEqual([
        call(f'cdbg/debuggees/{debuggee_id}'),
        call(f'cdbg/breakpoints/{debuggee_id}/active')
    ], mock_db_ref.call_args_list)

  # TODO: testStartRegisterRetry
  # TODO: testStartSubscribeRetry - Note: failures don't require retrying registration.

  @patch('firebase_admin.db.reference')
  @patch('firebase_admin.initialize_app')
  def testBreakpointSubscription(self, mock_initialize_app, mock_db_ref):
    mock_register_ref = MagicMock()
    fake_subscribe_ref = FakeReference()
    mock_db_ref.side_effect = [mock_register_ref, fake_subscribe_ref]

    # This class will keep track of the breakpoint updates and check them against expectations.
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
    fake_subscribe_ref.update('put', '/',
                              {breakpoints[0]['id']: breakpoints[0]})
    fake_subscribe_ref.update('patch', '/',
                              {breakpoints[1]['id']: breakpoints[1]})
    fake_subscribe_ref.update('put', f'/{breakpoints[2]["id"]}', breakpoints[2])
    fake_subscribe_ref.update('put', f'/{breakpoints[0]["id"]}', None)

    self.assertEqual(len(expected_results), result_checker._change_count)

  def asdftestTransmitBreakpointUpdateSuccess(self):
    self._Start()
    self._client.EnqueueBreakpointUpdate({'id': 'A'})
    while not self._update_execute.call_count:
      self._SkipIterations()
    self.assertEmpty(self._client._transmission_queue)

  def asdftestPoisonousMessage(self):
    self._update_execute.side_effect = HttpErrorTimeout()
    self._Start()
    self._SkipIterations(5)
    self._client.EnqueueBreakpointUpdate({'id': 'A'})
    while self._update_execute.call_count < 10:
      self._SkipIterations()
    self._SkipIterations(10)
    self.assertEmpty(self._client._transmission_queue)

  def asdftestTransmitBreakpointUpdateSocketError(self):
    # It would be nice to ensure that the retries will succeed if the error
    # stops, but that would make this test setup flaky.
    self._update_execute.side_effect = HttpConnectionReset()
    self._Start()
    self._client.EnqueueBreakpointUpdate({'id': 'A'})
    while self._update_execute.call_count < 10:
      self._SkipIterations()
    self._SkipIterations(10)
    self.assertEmpty(self._client._transmission_queue)

  def _TestInitializeLabels(self, module_var, version_var, minor_var):
    self._Start()

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

  def asdftestInitializeLegacyDebuggeeLabels(self):
    self._TestInitializeLabels('GAE_MODULE_NAME', 'GAE_MODULE_VERSION',
                               'GAE_MINOR_VERSION')

  def asdftestInitializeDebuggeeLabels(self):
    self._TestInitializeLabels('GAE_SERVICE', 'GAE_VERSION',
                               'GAE_DEPLOYMENT_ID')

  def asdftestInitializeCloudRunDebuggeeLabels(self):
    self._Start()

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

  def asdftestInitializeCloudFunctionDebuggeeLabels(self):
    self._Start()

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

  def asdftestInitializeCloudFunctionUnversionedDebuggeeLabels(self):
    self._Start()

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

  def asdftestInitializeCloudFunctionWithRegionDebuggeeLabels(self):
    self._Start()

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

  def asdftestAppFilesUniquifierNoMinorVersion(self):
    """Verify that uniquifier_computer is used if minor version not defined."""
    self._Start()

    root = tempfile.mkdtemp('', 'fake_app_')
    sys.path.insert(0, root)
    try:
      uniquifier1 = self._client._ComputeUniquifier({})

      with open(os.path.join(root, 'app.py'), 'w') as f:
        f.write('hello')
      uniquifier2 = self._client._ComputeUniquifier({})
    finally:
      del sys.path[0]

    self.assertNotEqual(uniquifier1, uniquifier2)

  def asdftestAppFilesUniquifierWithMinorVersion(self):
    """Verify that uniquifier_computer not used if minor version is defined."""
    self._Start()

    root = tempfile.mkdtemp('', 'fake_app_')

    os.environ['GAE_MINOR_VERSION'] = '12345'
    sys.path.insert(0, root)
    try:
      self._client.InitializeDebuggeeLabels(None)

      uniquifier1 = self._client._GetDebuggee()['uniquifier']

      with open(os.path.join(root, 'app.py'), 'w') as f:
        f.write('hello')
      uniquifier2 = self._client._GetDebuggee()['uniquifier']
    finally:
      del os.environ['GAE_MINOR_VERSION']
      del sys.path[0]

    self.assertEqual(uniquifier1, uniquifier2)

  def asdftestSourceContext(self):
    self._Start()

    root = tempfile.mkdtemp('', 'fake_app_')
    source_context_path = os.path.join(root, 'source-context.json')

    sys.path.insert(0, root)
    try:
      debuggee_no_source_context1 = self._client._GetDebuggee()

      with open(source_context_path, 'w') as f:
        f.write('not a valid JSON')
      debuggee_bad_source_context = self._client._GetDebuggee()

      with open(os.path.join(root, 'fake_app.py'), 'w') as f:
        f.write('pretend')
      debuggee_no_source_context2 = self._client._GetDebuggee()

      with open(source_context_path, 'w') as f:
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

  def _Start(self):
    self._client.SetupAuth()
    self._client.Start()

  def _OnIdle(self):
    self._iterations += 1

  def _SkipIterations(self, n=1):
    target = self._iterations + n
    while self._iterations < target:
      self._CheckTestTimeout()
      time.sleep(0.01)

  def _CheckTestTimeout(self):
    elapsed_time = datetime.datetime.utcnow() - self._start_time
    if elapsed_time > datetime.timedelta(seconds=15):
      self.fail('Test case timed out while waiting for state transition')

  def _BreakpointsChanged(self, breakpoints):
    self.breakpoints_changed_count += 1
    self.breakpoints = breakpoints

if __name__ == '__main__':
  absltest.main()
