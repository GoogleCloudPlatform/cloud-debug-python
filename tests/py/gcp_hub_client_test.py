"""Unit test for gcp_hub_client_test module."""

import datetime
import errno
import os
import socket
import sys
import tempfile
import time
from unittest import mock

from googleapiclient import discovery
from googleapiclient.errors import HttpError
from googleclouddebugger import version

import google.auth
from google.oauth2 import service_account
from absl.testing import absltest
from absl.testing import parameterized

from googleclouddebugger import gcp_hub_client

TEST_DEBUGGEE_ID = 'gcp:debuggee-id'
TEST_AGENT_ID = 'abc-123-d4'
TEST_PROJECT_ID = 'test-project-id'
TEST_PROJECT_NUMBER = '123456789'
TEST_SERVICE_ACCOUNT_EMAIL = 'a@developer.gserviceaccount.com'


class HttpResponse(object):

  def __init__(self, status):
    self.status = status
    self.reason = None


def HttpErrorTimeout():
  return HttpError(HttpResponse(408), b'Fake timeout')


def HttpConnectionReset():
  return socket.error(errno.ECONNRESET, 'Fake connection reset')


class GcpHubClientTest(parameterized.TestCase):
  """Simulates service account authentication."""

  def setUp(self):
    version.__version__ = 'test'

    self._client = gcp_hub_client.GcpHubClient()

    for backoff in [
        self._client.register_backoff, self._client.list_backoff,
        self._client.update_backoff
    ]:
      backoff.min_interval_sec /= 100000.0
      backoff.max_interval_sec /= 100000.0
      backoff._current_interval_sec /= 100000.0

    self._client.on_idle = self._OnIdle
    self._client.on_active_breakpoints_changed = mock.Mock()

    patcher = mock.patch.object(google.auth, 'default')
    self._default_auth_mock = patcher.start()
    self._default_auth_mock.return_value = (None, TEST_PROJECT_ID)
    self.addCleanup(patcher.stop)

    self._service = mock.Mock()
    self._iterations = 0

    patcher = mock.patch.object(discovery, 'build')
    self._mock_build = patcher.start()
    self._mock_build.return_value = self._service
    self.addCleanup(patcher.stop)

    controller = self._service.controller.return_value
    debuggees = controller.debuggees.return_value
    breakpoints = debuggees.breakpoints.return_value
    self._register_call = debuggees.register
    self._register_execute = self._register_call.return_value.execute
    self._list_call = breakpoints.list
    self._list_execute = self._list_call.return_value.execute
    self._update_execute = breakpoints.update.return_value.execute

    # Default responses for API requests.
    self._register_execute.return_value = {
        'debuggee': {
            'id': TEST_DEBUGGEE_ID,
            'project': TEST_PROJECT_NUMBER,
        },
        'agentId': TEST_AGENT_ID,
    }
    self._list_execute.return_value = {}

    self._start_time = datetime.datetime.utcnow()

  def tearDown(self):
    self._client.Stop()

  def testDefaultAuth(self):
    self._client.SetupAuth()

    self._default_auth_mock.assert_called_with(
        scopes=['https://www.googleapis.com/auth/cloud-platform'])
    self.assertEqual(TEST_PROJECT_ID, self._client._project_id)
    self.assertEqual(TEST_PROJECT_ID, self._client._project_number)

  def testOverrideProjectIdNumber(self):
    project_id = 'project2'
    project_number = '456'
    self._client.SetupAuth(project_id=project_id, project_number=project_number)

    self._default_auth_mock.assert_called_with(
        scopes=['https://www.googleapis.com/auth/cloud-platform'])
    self.assertEqual(project_id, self._client._project_id)
    self.assertEqual(project_number, self._client._project_number)

  def testServiceAccountJsonAuth(self):
    with mock.patch.object(
        service_account.Credentials,
        'from_service_account_file') as from_service_account_file:
      json_file = tempfile.NamedTemporaryFile()
      with open(json_file.name, 'w') as f:
        f.write('{"project_id": "%s"}' % TEST_PROJECT_ID)
      self._client.SetupAuth(service_account_json_file=json_file.name)

    self._default_auth_mock.assert_not_called()
    from_service_account_file.assert_called_with(
        json_file.name,
        scopes=['https://www.googleapis.com/auth/cloud-platform'])
    self.assertEqual(TEST_PROJECT_ID, self._client._project_id)
    self.assertEqual(TEST_PROJECT_ID, self._client._project_number)

  def testNoProjectId(self):
    self._default_auth_mock.return_value = (None, None)

    with self.assertRaises(gcp_hub_client.NoProjectIdError):
      self._Start()

  def testContinuousSuccess(self):
    self._Start()
    self._SkipIterations(10)
    self.assertTrue(self._mock_build.called)
    self.assertEqual(TEST_PROJECT_NUMBER, self._client._project_number)

  def testBreakpointsChanged(self):
    self._Start()
    self._SkipIterations(5)
    self.assertEqual(0, self._client.on_active_breakpoints_changed.call_count)

    self._list_execute.return_value = ({'breakpoints': [{'id': 'bp1'}]})
    self._SkipIterations()
    self.assertEqual(1, self._client.on_active_breakpoints_changed.call_count)

    self._list_execute.return_value = ({'breakpoints': [{'id': 'bp2'}]})
    self._SkipIterations()
    self.assertEqual(2, self._client.on_active_breakpoints_changed.call_count)

    self._list_execute.return_value = ({'breakpoints': [{}]})
    self._SkipIterations()
    self.assertEqual(3, self._client.on_active_breakpoints_changed.call_count)

  @parameterized.named_parameters(
      ('DefaultEnabled', True, True, 'CANARY_MODE_DEFAULT_ENABLED'),
      ('AlwaysEnabled', True, False, 'CANARY_MODE_ALWAYS_ENABLED'),
      ('DefaultDisabled', False, True, 'CANARY_MODE_DEFAULT_DISABLED'),
      ('AlwaysDisabled', False, False, 'CANARY_MODE_ALWAYS_DISABLED'),
      ('AlwaysEnabledWithStringFlags', 'True',
       'a-value-should-be-treated-as-false', 'CANARY_MODE_ALWAYS_ENABLED'))
  def testRegisterDebuggeeCanaryMode(self, breakpoint_enable_canary,
                                     breakpoint_allow_canary_override,
                                     expected_canary_mode):
    self._client.SetupCanaryMode(breakpoint_enable_canary,
                                 breakpoint_allow_canary_override)
    self._Start()
    self._SkipIterations(5)
    self.assertEqual(
        expected_canary_mode,
        self._register_call.call_args[1]['body']['debuggee']['canaryMode'])

  def testRegisterDebuggeeFailure(self):
    self._register_execute.side_effect = HttpErrorTimeout()
    self._Start()
    self._SkipIterations(5)
    self.assertGreaterEqual(self._register_execute.call_count, 5)

  def testListActiveBreakpointsFailure(self):
    self._Start()
    self._SkipIterations(5)
    self.assertEqual(1, self._register_execute.call_count)

    # If the these 2 lines are executed between _ListActiveBreakpoints() and
    # on_idle() in _MainThreadProc, then there will be 1 iteration incremented
    # where _ListActiveBreakpoints is still a success and registration is not
    # required, leading to only 4 _register_execute calls instead of 5.
    self._list_execute.side_effect = HttpErrorTimeout()
    self._SkipIterations(5)

    self.assertGreaterEqual(self._register_execute.call_count, 4)

  def testListActiveBreakpointsNoUpdate(self):
    self._Start()
    self._SkipIterations(5)
    self.assertEqual(1, self._register_execute.call_count)
    self.assertEqual(0, self._client.on_active_breakpoints_changed.call_count)

    self._list_execute.return_value = ({'breakpoints': [{'id': 'bp1'}]})
    self._SkipIterations()
    self.assertEqual(1, self._client.on_active_breakpoints_changed.call_count)

    self._list_execute.return_value = ({'waitExpired': 'True'})
    self._SkipIterations(20)
    self.assertEqual(1, self._register_execute.call_count)
    self.assertEqual(1, self._client.on_active_breakpoints_changed.call_count)

  def testListActiveBreakpointsSendAgentId(self):
    self._Start()
    self._SkipIterations(5)
    self.assertEqual(1, self._register_execute.call_count)
    self.assertGreater(self._list_execute.call_count, 0)
    self.assertEqual(TEST_AGENT_ID, self._list_call.call_args[1]['agentId'])

  def testTransmitBreakpointUpdateSuccess(self):
    self._Start()
    self._client.EnqueueBreakpointUpdate({'id': 'A'})
    while not self._update_execute.call_count:
      self._SkipIterations()
    self.assertEmpty(self._client._transmission_queue)

  def testPoisonousMessage(self):
    self._update_execute.side_effect = HttpErrorTimeout()
    self._Start()
    self._SkipIterations(5)
    self._client.EnqueueBreakpointUpdate({'id': 'A'})
    while self._update_execute.call_count < 10:
      self._SkipIterations()
    self._SkipIterations(10)
    self.assertEmpty(self._client._transmission_queue)

  def testTransmitBreakpointUpdateSocketError(self):
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

  def testInitializeLegacyDebuggeeLabels(self):
    self._TestInitializeLabels('GAE_MODULE_NAME', 'GAE_MODULE_VERSION',
                               'GAE_MINOR_VERSION')

  def testInitializeDebuggeeLabels(self):
    self._TestInitializeLabels('GAE_SERVICE', 'GAE_VERSION',
                               'GAE_DEPLOYMENT_ID')

  def testInitializeCloudRunDebuggeeLabels(self):
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

  def testInitializeCloudFunctionDebuggeeLabels(self):
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

  def testInitializeCloudFunctionUnversionedDebuggeeLabels(self):
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

  def testInitializeCloudFunctionWithRegionDebuggeeLabels(self):
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

  def testAppFilesUniquifierNoMinorVersion(self):
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

  def testAppFilesUniquifierWithMinorVersion(self):
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

  def testSourceContext(self):
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


if __name__ == '__main__':
  absltest.main()
