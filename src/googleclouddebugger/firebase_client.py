# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Communicates with Firebase RTDB backend."""

from collections import deque
import copy
import hashlib
import json
import os
import platform
import requests
import sys
import threading
import time
import traceback

import firebase_admin
import firebase_admin.credentials
import firebase_admin.db
import firebase_admin.exceptions

from . import backoff
from . import cdbg_native as native
from . import labels
from . import uniquifier_computer
from . import application_info
from . import version
# This module catches all exception. This is safe because it runs in
# a daemon thread (so we are not blocking Ctrl+C). We need to catch all
# the exception because HTTP client is unpredictable as far as every
# exception it can throw.
# pylint: disable=broad-except

# Set of all known debuggee labels (passed down as flags). The value of
# a map is optional environment variable that can be used to set the flag
# (flags still take precedence).
_DEBUGGEE_LABELS = {
    labels.Debuggee.MODULE: [
        'GAE_SERVICE', 'GAE_MODULE_NAME', 'K_SERVICE', 'FUNCTION_NAME'
    ],
    labels.Debuggee.VERSION: [
        'GAE_VERSION', 'GAE_MODULE_VERSION', 'K_REVISION',
        'X_GOOGLE_FUNCTION_VERSION'
    ],
    labels.Debuggee.MINOR_VERSION: ['GAE_DEPLOYMENT_ID', 'GAE_MINOR_VERSION']
}

# Debuggee labels used to format debuggee description (ordered). The minor
# version is excluded for the sake of consistency with AppEngine UX.
_DESCRIPTION_LABELS = [
    labels.Debuggee.PROJECT_ID, labels.Debuggee.MODULE, labels.Debuggee.VERSION
]

_METADATA_SERVER_URL = 'http://metadata.google.internal/computeMetadata/v1'

_TRANSIENT_ERROR_CODES = ('UNKNOWN', 'INTERNAL', 'N/A', 'UNAVAILABLE',
                          'DEADLINE_EXCEEDED', 'RESOURCE_EXHAUSTED',
                          'UNAUTHENTICATED', 'PERMISSION_DENIED')


class NoProjectIdError(Exception):
  """Used to indicate the project id cannot be determined."""


class FirebaseClient(object):
  """Firebase RTDB Backend client.

  Registers the debuggee, subscribes for active breakpoints and sends breakpoint
  updates to the backend.

  This class supports two types of authentication: application default
  credentials or a manually provided JSON credentials file for a service
  account.

  FirebaseClient creates a worker thread that communicates with the backend. The
  thread can be stopped with a Stop function, but it is optional since the
  worker thread is marked as daemon.
  """

  def __init__(self):
    self.on_active_breakpoints_changed = lambda x: None
    self.on_idle = lambda: None
    self._debuggee_labels = {}
    self._credentials = None
    self._project_id = None
    self._database_url = None
    self._debuggee_id = None
    self._canary_mode = None
    self._breakpoints = {}
    self._main_thread = None
    self._transmission_thread = None
    self._transmission_thread_startup_lock = threading.Lock()
    self._transmission_queue = deque(maxlen=100)
    self._new_updates = threading.Event()
    self._breakpoint_subscription = None

    # Events for unit testing.
    self.registration_complete = threading.Event()
    self.subscription_complete = threading.Event()

    #
    # Configuration options (constants only modified by unit test)
    #

    # Delay before retrying failed request.
    self.register_backoff = backoff.Backoff()  # Register debuggee.
    self.subscribe_backoff = backoff.Backoff()  # Subscribe to updates.
    self.update_backoff = backoff.Backoff()  # Update breakpoint.

    # Maximum number of times that the message is re-transmitted before it
    # is assumed to be poisonous and discarded
    self.max_transmit_attempts = 10

  def InitializeDebuggeeLabels(self, flags):
    """Initialize debuggee labels from environment variables and flags.

    The caller passes all the flags that the debuglet got. This function
    will only use the flags used to label the debuggee. Flags take precedence
    over environment variables.

    Debuggee description is formatted from available flags.

    Args:
      flags: dictionary of debuglet command line flags.
    """
    self._debuggee_labels = {}

    for (label, var_names) in _DEBUGGEE_LABELS.items():
      # var_names is a list of possible environment variables that may contain
      # the label value. Find the first one that is set.
      for name in var_names:
        value = os.environ.get(name)
        if value:
          # Special case for module. We omit the "default" module
          # to stay consistent with AppEngine.
          if label == labels.Debuggee.MODULE and value == 'default':
            break
          self._debuggee_labels[label] = value
          break

    # Special case when FUNCTION_NAME is set and X_GOOGLE_FUNCTION_VERSION
    # isn't set. We set the version to 'unversioned' to be consistent with other
    # agents.
    # TODO: Stop assigning 'unversioned' to a GCF and find the
    # actual version.
    if ('FUNCTION_NAME' in os.environ and
        labels.Debuggee.VERSION not in self._debuggee_labels):
      self._debuggee_labels[labels.Debuggee.VERSION] = 'unversioned'

    if flags:
      self._debuggee_labels.update({
          name: value
          for (name, value) in flags.items()
          if name in _DEBUGGEE_LABELS
      })

    self._debuggee_labels[labels.Debuggee.PROJECT_ID] = self._project_id

    platform_enum = application_info.GetPlatform()
    self._debuggee_labels[labels.Debuggee.PLATFORM] = platform_enum.value

    if platform_enum == application_info.PlatformType.CLOUD_FUNCTION:
      region = application_info.GetRegion()
      if region:
        self._debuggee_labels[labels.Debuggee.REGION] = region

  def SetupAuth(self,
                project_id=None,
                service_account_json_file=None,
                database_url=None):
    """Sets up authentication with Google APIs.

    This will use the credentials from service_account_json_file if provided,
    falling back to application default credentials.
    See https://cloud.google.com/docs/authentication/production.

    Args:
      project_id: GCP project ID (e.g. myproject). If not provided, will attempt
          to retrieve it from the credentials.
      service_account_json_file: JSON file to use for credentials. If not
          provided, will default to application default credentials.
      database_url: Firebase realtime database URL to be used.  If not
          provided, will default to https://{project_id}-cdbg.firebaseio.com
    Raises:
      NoProjectIdError: If the project id cannot be determined.
    """
    if service_account_json_file:
      self._credentials = firebase_admin.credentials.Certificate(
          service_account_json_file)
      if not project_id:
        with open(service_account_json_file, encoding='utf-8') as f:
          project_id = json.load(f).get('project_id')
    else:
      if not project_id:
        try:
          r = requests.get(
              f'{_METADATA_SERVER_URL}/project/project-id',
              headers={'Metadata-Flavor': 'Google'})
          project_id = r.text
        except requests.exceptions.RequestException:
          native.LogInfo('Metadata server not available')

    if not project_id:
      raise NoProjectIdError(
          'Unable to determine the project id from the API credentials. '
          'Please specify the project id using the --project_id flag.')

    self._project_id = project_id

    if database_url:
      self._database_url = database_url
    else:
      self._database_url = f'https://{self._project_id}-cdbg.firebaseio.com'

  def Start(self):
    """Starts the worker thread."""
    self._shutdown = False

    # Spin up the main thread which will create the other necessary threads.
    self._main_thread = threading.Thread(target=self._MainThreadProc)
    self._main_thread.name = 'Cloud Debugger main worker thread'
    self._main_thread.daemon = True
    self._main_thread.start()

  def Stop(self):
    """Signals the worker threads to shut down and waits until it exits."""
    self._shutdown = True
    self._new_updates.set()  # Wake up the transmission thread.

    if self._main_thread is not None:
      self._main_thread.join()
      self._main_thread = None

    if self._transmission_thread is not None:
      self._transmission_thread.join()
      self._transmission_thread = None

    if self._breakpoint_subscription is not None:
      self._breakpoint_subscription.close()
      self._breakpoint_subscription = None

  def EnqueueBreakpointUpdate(self, breakpoint_data):
    """Asynchronously updates the specified breakpoint on the backend.

    This function returns immediately. The worker thread is actually doing
    all the work. The worker thread is responsible to retry the transmission
    in case of transient errors.

    The assumption is that the breakpoint is moving from Active to Final state.

    Args:
      breakpoint: breakpoint in either final or non-final state.
    """
    with self._transmission_thread_startup_lock:
      if self._transmission_thread is None:
        self._transmission_thread = threading.Thread(
            target=self._TransmissionThreadProc)
        self._transmission_thread.name = 'Cloud Debugger transmission thread'
        self._transmission_thread.daemon = True
        self._transmission_thread.start()

    self._transmission_queue.append((breakpoint_data, 0))
    self._new_updates.set()  # Wake up the worker thread to send immediately.

  def _MainThreadProc(self):
    """Entry point for the worker thread.

    This thread only serves to register and kick off the firebase subscription
    which will run in its own thread.  That thread will be owned by
    self._breakpoint_subscription.
    """
    # Note: if self._credentials is None, default app credentials will be used.
    try:
      firebase_admin.initialize_app(self._credentials,
                                    {'databaseURL': self._database_url})
    except ValueError:
      native.LogWarning(
          f'Failed to initialize firebase: {traceback.format_exc()}')
      native.LogError('Failed to start debugger agent.  Giving up.')
      return

    registration_required, delay = True, 0
    while registration_required:
      time.sleep(delay)
      registration_required, delay = self._RegisterDebuggee()
    self.registration_complete.set()

    subscription_required, delay = True, 0
    while subscription_required:
      time.sleep(delay)
      subscription_required, delay = self._SubscribeToBreakpoints()
    self.subscription_complete.set()

  def _TransmissionThreadProc(self):
    """Entry point for the transmission worker thread."""

    while not self._shutdown:
      self._new_updates.clear()

      delay = self._TransmitBreakpointUpdates()

      self._new_updates.wait(delay)

  def _RegisterDebuggee(self):
    """Single attempt to register the debuggee.

    If the registration succeeds, sets self._debuggee_id to the registered
    debuggee ID.

    Args:
      service: client to use for API calls

    Returns:
      (registration_required, delay) tuple
    """
    debuggee = None
    try:
      debuggee = self._GetDebuggee()
      self._debuggee_id = debuggee['id']
    except BaseException:
      native.LogWarning(
          f'Debuggee information not available: {traceback.format_exc()}')
      return (True, self.register_backoff.Failed())

    try:
      debuggee_path = f'cdbg/debuggees/{self._debuggee_id}'
      native.LogInfo(
          f'registering at {self._database_url}, path: {debuggee_path}')
      firebase_admin.db.reference(debuggee_path).set(debuggee)
      native.LogInfo(
          f'Debuggee registered successfully, ID: {self._debuggee_id}')
      self.register_backoff.Succeeded()
      return (False, 0)  # Proceed immediately to subscribing to breakpoints.
    except BaseException:
      # There is no significant benefit to handing different exceptions
      # in different ways; we will log and retry regardless.
      native.LogInfo(f'Failed to register debuggee: {traceback.format_exc()}')
      return (True, self.register_backoff.Failed())

  def _SubscribeToBreakpoints(self):
    # Kill any previous subscriptions first.
    if self._breakpoint_subscription is not None:
      self._breakpoint_subscription.close()
      self._breakpoint_subscription = None

    path = f'cdbg/breakpoints/{self._debuggee_id}/active'
    native.LogInfo(f'Subscribing to breakpoint updates at {path}')
    ref = firebase_admin.db.reference(path)
    try:
      self._breakpoint_subscription = ref.listen(self._ActiveBreakpointCallback)
      return (False, 0)
    except firebase_admin.exceptions.FirebaseError:
      native.LogInfo(
          f'Failed to subscribe to breakpoints: {traceback.format_exc()}')
      return (True, self.subscribe_backoff.Failed())

  def _ActiveBreakpointCallback(self, event):
    if event.event_type == 'put':
      if event.data is None:
        # Either deleting a breakpoint or initializing with no breakpoints.
        # Initializing with no breakpoints is a no-op.
        # If deleting, event.path will be /{breakpointid}
        if event.path != '/':
          breakpoint_id = event.path[1:]
          del self._breakpoints[breakpoint_id]
      else:
        if event.path == '/':
          # New set of breakpoints.
          self._breakpoints = {}
          for (key, value) in event.data.items():
            self._AddBreakpoint(key, value)
        else:
          # New breakpoint.
          breakpoint_id = event.path[1:]
          self._AddBreakpoint(breakpoint_id, event.data)

    elif event.event_type == 'patch':
      # New breakpoint or breakpoints.
      for (key, value) in event.data.items():
        self._AddBreakpoint(key, value)
    else:
      native.LogWarning('Unexpected event from Firebase: '
                        f'{event.event_type} {event.path} {event.data}')
      return

    native.LogInfo(f'Breakpoints list changed, {len(self._breakpoints)} active')
    self.on_active_breakpoints_changed(list(self._breakpoints.values()))

  def _AddBreakpoint(self, breakpoint_id, breakpoint_data):
    breakpoint_data['id'] = breakpoint_id
    self._breakpoints[breakpoint_id] = breakpoint_data

  def _TransmitBreakpointUpdates(self):
    """Tries to send pending breakpoint updates to the backend.

    Sends all the pending breakpoint updates. In case of transient failures,
    the breakpoint is inserted back to the top of the queue. Application
    failures are not retried (for example updating breakpoint in a final
    state).

    Each pending breakpoint maintains a retry counter. After repeated transient
    failures the breakpoint is discarded and dropped from the queue.

    Args:
      service: client to use for API calls

    Returns:
      (reconnect, timeout) tuple. The first element ("reconnect") is set to
      true on unexpected HTTP responses. The caller should discard the HTTP
      connection and create a new one. The second element ("timeout") is
      set to None if all pending breakpoints were sent successfully. Otherwise
      returns time interval in seconds to stall before retrying.
    """
    retry_list = []

    # There is only one consumer, so two step pop is safe.
    while self._transmission_queue:
      breakpoint_data, retry_count = self._transmission_queue.popleft()

      bp_id = breakpoint_data['id']

      try:
        # Something has changed on the breakpoint.
        # It should be going from active to final, but let's make sure.
        if not breakpoint_data.get('isFinalState', False):
          raise BaseException(
              f'Unexpected breakpoint update requested: {breakpoint_data}')

        # If action is missing, it should be set to 'CAPTURE'
        is_logpoint = breakpoint_data.get('action') == 'LOG'
        is_snapshot = not is_logpoint
        if is_snapshot:
          breakpoint_data['action'] = 'CAPTURE'

        # Set the completion time on the server side using a magic value.
        breakpoint_data['finalTimeUnixMsec'] = {'.sv': 'timestamp'}

        # First, remove from the active breakpoints.
        bp_ref = firebase_admin.db.reference(
            f'cdbg/breakpoints/{self._debuggee_id}/active/{bp_id}')
        bp_ref.delete()

        summary_data = breakpoint_data
        # Save snapshot data for snapshots only.
        if is_snapshot:
          # Note that there may not be snapshot data.
          bp_ref = firebase_admin.db.reference(
              f'cdbg/breakpoints/{self._debuggee_id}/snapshot/{bp_id}')
          bp_ref.set(breakpoint_data)

          # Now strip potential snapshot data.
          summary_data = copy.deepcopy(breakpoint_data)
          summary_data.pop('evaluatedExpressions', None)
          summary_data.pop('stackFrames', None)
          summary_data.pop('variableTable', None)

        # Then add it to the list of final breakpoints.
        bp_ref = firebase_admin.db.reference(
            f'cdbg/breakpoints/{self._debuggee_id}/final/{bp_id}')
        bp_ref.set(summary_data)

        native.LogInfo(f'Breakpoint {bp_id} update transmitted successfully')

      except firebase_admin.exceptions.FirebaseError as err:
        if err.code in _TRANSIENT_ERROR_CODES:
          if retry_count < self.max_transmit_attempts - 1:
            native.LogInfo(f'Failed to send breakpoint {bp_id} update: '
                           f'{traceback.format_exc()}')
            retry_list.append((breakpoint_data, retry_count + 1))
          else:
            native.LogWarning(
                f'Breakpoint {bp_id} retry count exceeded maximum')
        else:
          # This is very common if multiple instances are sending final update
          # simultaneously.
          native.LogInfo(f'{err}, breakpoint: {bp_id}')

      except BaseException:
        native.LogWarning(f'Fatal error sending breakpoint {bp_id} update: '
                          f'{traceback.format_exc()}')

    self._transmission_queue.extend(retry_list)

    if not self._transmission_queue:
      self.update_backoff.Succeeded()
      # Nothing to send, wait until next breakpoint update.
      return None
    else:
      return self.update_backoff.Failed()

  def _GetDebuggee(self):
    """Builds the debuggee structure."""
    major_version = version.__version__.split('.', maxsplit=1)[0]
    python_version = ''.join(platform.python_version().split('.')[:2])
    agent_version = f'google.com/python{python_version}-gcp/v{major_version}'

    debuggee = {
        'description': self._GetDebuggeeDescription(),
        'labels': self._debuggee_labels,
        'agentVersion': agent_version,
    }

    source_context = self._ReadAppJsonFile('source-context.json')
    if source_context:
      debuggee['sourceContexts'] = [source_context]

    debuggee['uniquifier'] = self._ComputeUniquifier(debuggee)

    debuggee['id'] = self._ComputeDebuggeeId(debuggee)

    return debuggee

  def _ComputeDebuggeeId(self, debuggee):
    """Computes a debuggee ID.

    The debuggee ID has to be identical on all instances.  Therefore the
    ID should not include any random elements or elements that may be
    different on different instances.

    Args:
      debuggee: complete debuggee message (including uniquifier)

    Returns:
      Debuggee ID meeting the criteria described above.
    """
    fullhash = hashlib.sha1(json.dumps(debuggee,
                                       sort_keys=True).encode()).hexdigest()
    return f'd-{fullhash[:8]}'

  def _GetDebuggeeDescription(self):
    """Formats debuggee description based on debuggee labels."""
    return '-'.join(self._debuggee_labels[label]
                    for label in _DESCRIPTION_LABELS
                    if label in self._debuggee_labels)

  def _ComputeUniquifier(self, debuggee):
    """Computes debuggee uniquifier.

    The debuggee uniquifier has to be identical on all instances. Therefore the
    uniquifier should not include any random numbers and should only be based
    on inputs that are guaranteed to be the same on all instances.

    Args:
      debuggee: complete debuggee message without the uniquifier

    Returns:
      Hex string of SHA1 hash of project information, debuggee labels and
      debuglet version.
    """
    uniquifier = hashlib.sha1()

    # Compute hash of application files if we don't have source context. This
    # way we can still distinguish between different deployments.
    if ('minorversion' not in debuggee.get('labels', []) and
        'sourceContexts' not in debuggee):
      uniquifier_computer.ComputeApplicationUniquifier(uniquifier)

    return uniquifier.hexdigest()

  def _ReadAppJsonFile(self, relative_path):
    """Reads JSON file from an application directory.

    Args:
      relative_path: file name relative to application root directory.

    Returns:
      Parsed JSON data or None if the file does not exist, can't be read or
      not a valid JSON file.
    """
    try:
      with open(
          os.path.join(sys.path[0], relative_path), 'r', encoding='utf-8') as f:
        return json.load(f)
    except (IOError, ValueError):
      return None
