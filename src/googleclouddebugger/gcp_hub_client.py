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
"""Communicates with Cloud Debugger backend over HTTP."""

from collections import deque
import copy
import hashlib
import inspect
import json
import logging
import os
import platform
import socket
import sys
import threading
import time
import traceback

import google_auth_httplib2
import googleapiclient
import googleapiclient.discovery
import httplib2
import six

import google.auth
from google.oauth2 import service_account

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

# API scope we are requesting when service account authentication is enabled.
_CLOUD_PLATFORM_SCOPE = ['https://www.googleapis.com/auth/cloud-platform']

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

# HTTP timeout when accessing the cloud debugger API. It is selected to be
# longer than the typical controller.breakpoints.list hanging get latency
# of 40 seconds.
_HTTP_TIMEOUT_SECONDS = 100

# The map from the values of flags (breakpoint_enable_canary,
# breakpoint_allow_canary_override) to canary mode.
_CANARY_MODE_MAP = {
    (True, True): 'CANARY_MODE_DEFAULT_ENABLED',
    (True, False): 'CANARY_MODE_ALWAYS_ENABLED',
    (False, True): 'CANARY_MODE_DEFAULT_DISABLED',
    (False, False): 'CANARY_MODE_ALWAYS_DISABLED',
}


class NoProjectIdError(Exception):
  """Used to indicate the project id cannot be determined."""


class GcpHubClient(object):
  """Controller API client.

  Registers the debuggee, queries the active breakpoints and sends breakpoint
  updates to the backend.

  This class supports two types of authentication: application default
  credentials or a manually provided JSON credentials file for a service
  account.

  GcpHubClient creates a worker thread that communicates with the backend. The
  thread can be stopped with a Stop function, but it is optional since the
  worker thread is marked as daemon.
  """

  def __init__(self):
    self.on_active_breakpoints_changed = lambda x: None
    self.on_idle = lambda: None
    self._debuggee_labels = {}
    self._service_account_auth = False
    self._debuggee_id = None
    self._agent_id = None
    self._canary_mode = None
    self._wait_token = 'init'
    self._breakpoints = []
    self._main_thread = None
    self._transmission_thread = None
    self._transmission_thread_startup_lock = threading.Lock()
    self._transmission_queue = deque(maxlen=100)
    self._new_updates = threading.Event()

    # Disable logging in the discovery API to avoid excessive logging.
    class _ChildLogFilter(logging.Filter):
      """Filter to eliminate info-level logging when called from this module."""

      def __init__(self, filter_levels=None):
        super(_ChildLogFilter, self).__init__()
        self._filter_levels = filter_levels or set(logging.INFO)
        # Get name without extension to avoid .py vs .pyc issues
        self._my_filename = os.path.splitext(
            inspect.getmodule(_ChildLogFilter).__file__)[0]

      def filter(self, record):
        if record.levelno not in self._filter_levels:
          return True
        callerframes = inspect.getouterframes(inspect.currentframe())
        for f in callerframes:
          if os.path.splitext(f[1])[0] == self._my_filename:
            return False
        return True

    self._log_filter = _ChildLogFilter({logging.INFO})
    googleapiclient.discovery.logger.addFilter(self._log_filter)

    #
    # Configuration options (constants only modified by unit test)
    #

    # Delay before retrying failed request.
    self.register_backoff = backoff.Backoff()  # Register debuggee.
    self.list_backoff = backoff.Backoff()  # Query active breakpoints.
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

    for (label, var_names) in six.iteritems(_DEBUGGEE_LABELS):
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
          for (name, value) in six.iteritems(flags)
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
                project_number=None,
                service_account_json_file=None):
    """Sets up authentication with Google APIs.

    This will use the credentials from service_account_json_file if provided,
    falling back to application default credentials.
    See https://cloud.google.com/docs/authentication/production.

    Args:
      project_id: GCP project ID (e.g. myproject). If not provided, will attempt
          to retrieve it from the credentials.
      project_number: GCP project number (e.g. 72386324623). If not provided,
          project_id will be used in its place.
      service_account_json_file: JSON file to use for credentials. If not
          provided, will default to application default credentials.
    Raises:
      NoProjectIdError: If the project id cannot be determined.
    """
    if service_account_json_file:
      self._credentials = (
          service_account.Credentials.from_service_account_file(
              service_account_json_file, scopes=_CLOUD_PLATFORM_SCOPE))
      if not project_id:
        with open(service_account_json_file) as f:
          project_id = json.load(f).get('project_id')
    else:
      self._credentials, credentials_project_id = google.auth.default(
          scopes=_CLOUD_PLATFORM_SCOPE)
      project_id = project_id or credentials_project_id

    if not project_id:
      raise NoProjectIdError(
          'Unable to determine the project id from the API credentials. '
          'Please specify the project id using the --project_id flag.')

    self._project_id = project_id
    self._project_number = project_number or project_id

  def SetupCanaryMode(self, breakpoint_enable_canary,
                      breakpoint_allow_canary_override):
    """Sets up canaryMode for the debuggee according to input parameters.

    Args:
      breakpoint_enable_canary: str or bool, whether to enable breakpoint
          canary. Any string except 'True' is interpreted as False.
      breakpoint_allow_canary_override: str or bool, whether to allow the
          individually set breakpoint to override the canary behavior. Any
          string except 'True' is interpreted as False.
    """
    enable_canary = breakpoint_enable_canary in ('True', True)
    allow_canary_override = breakpoint_allow_canary_override in ('True', True)
    self._canary_mode = _CANARY_MODE_MAP[enable_canary, allow_canary_override]

  def Start(self):
    """Starts the worker thread."""
    self._shutdown = False

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

  def EnqueueBreakpointUpdate(self, breakpoint):
    """Asynchronously updates the specified breakpoint on the backend.

    This function returns immediately. The worker thread is actually doing
    all the work. The worker thread is responsible to retry the transmission
    in case of transient errors.

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

    self._transmission_queue.append((breakpoint, 0))
    self._new_updates.set()  # Wake up the worker thread to send immediately.

  def _BuildService(self):
    http = httplib2.Http(timeout=_HTTP_TIMEOUT_SECONDS)
    http = google_auth_httplib2.AuthorizedHttp(self._credentials, http)

    api = googleapiclient.discovery.build(
        'clouddebugger', 'v2', http=http, cache_discovery=False)
    return api.controller()

  def _MainThreadProc(self):
    """Entry point for the worker thread."""
    registration_required = True
    while not self._shutdown:
      if registration_required:
        service = self._BuildService()
        registration_required, delay = self._RegisterDebuggee(service)

      if not registration_required:
        registration_required, delay = self._ListActiveBreakpoints(service)

      if self.on_idle is not None:
        self.on_idle()

      if not self._shutdown:
        time.sleep(delay)

  def _TransmissionThreadProc(self):
    """Entry point for the transmission worker thread."""
    reconnect = True

    while not self._shutdown:
      self._new_updates.clear()

      if reconnect:
        service = self._BuildService()
        reconnect = False

      reconnect, delay = self._TransmitBreakpointUpdates(service)

      self._new_updates.wait(delay)

  def _RegisterDebuggee(self, service):
    """Single attempt to register the debuggee.

    If the registration succeeds, sets self._debuggee_id to the registered
    debuggee ID.

    Args:
      service: client to use for API calls

    Returns:
      (registration_required, delay) tuple
    """
    try:
      request = {'debuggee': self._GetDebuggee()}

      try:
        response = service.debuggees().register(body=request).execute()

        # self._project_number will refer to the project id on initialization if
        # the project number is not available. The project field in the debuggee
        # will always refer to the project number. Update so the server will not
        # have to do id->number translations in the future.
        project_number = response['debuggee'].get('project')
        self._project_number = project_number or self._project_number

        self._debuggee_id = response['debuggee']['id']
        self._agent_id = response['agentId']
        native.LogInfo(
            'Debuggee registered successfully, ID: %s, agent ID: %s, '
            'canary mode: %s' % (self._debuggee_id, self._agent_id,
                                 response['debuggee'].get('canaryMode')))
        self.register_backoff.Succeeded()
        return (False, 0)  # Proceed immediately to list active breakpoints.
      except BaseException:
        native.LogInfo('Failed to register debuggee: %s, %s' %
                       (request, traceback.format_exc()))
    except BaseException:
      native.LogWarning('Debuggee information not available: ' +
                        traceback.format_exc())

    return (True, self.register_backoff.Failed())

  def _ListActiveBreakpoints(self, service):
    """Single attempt query the list of active breakpoints.

    Must not be called before the debuggee has been registered. If the request
    fails, this function resets self._debuggee_id, which triggers repeated
    debuggee registration.

    Args:
      service: client to use for API calls

    Returns:
      (registration_required, delay) tuple
    """
    try:
      response = service.debuggees().breakpoints().list(
          debuggeeId=self._debuggee_id,
          agentId=self._agent_id,
          waitToken=self._wait_token,
          successOnTimeout=True).execute()
      if not response.get('waitExpired'):
        self._wait_token = response.get('nextWaitToken')
        breakpoints = response.get('breakpoints') or []
        if self._breakpoints != breakpoints:
          self._breakpoints = breakpoints
          native.LogInfo('Breakpoints list changed, %d active, wait token: %s' %
                         (len(self._breakpoints), self._wait_token))
          self.on_active_breakpoints_changed(copy.deepcopy(self._breakpoints))
    except BaseException:
      native.LogInfo('Failed to query active breakpoints: ' +
                     traceback.format_exc())

      # Forget debuggee ID to trigger repeated debuggee registration. Once the
      # registration succeeds, the worker thread will retry this query
      self._debuggee_id = None

      return (True, self.list_backoff.Failed())

    self.list_backoff.Succeeded()
    return (False, 0)

  def _TransmitBreakpointUpdates(self, service):
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
    reconnect = False
    retry_list = []

    # There is only one consumer, so two step pop is safe.
    while self._transmission_queue:
      breakpoint, retry_count = self._transmission_queue.popleft()

      try:
        service.debuggees().breakpoints().update(
            debuggeeId=self._debuggee_id,
            id=breakpoint['id'],
            body={
                'breakpoint': breakpoint
            }).execute()

        native.LogInfo('Breakpoint %s update transmitted successfully' %
                       (breakpoint['id']))
      except googleapiclient.errors.HttpError as err:
        # Treat 400 error codes (except timeout) as application error that will
        # not be retried. All other errors are assumed to be transient.
        status = err.resp.status
        is_transient = ((status >= 500) or (status == 408))
        if is_transient:
          if retry_count < self.max_transmit_attempts - 1:
            native.LogInfo('Failed to send breakpoint %s update: %s' %
                           (breakpoint['id'], traceback.format_exc()))
            retry_list.append((breakpoint, retry_count + 1))
          else:
            native.LogWarning('Breakpoint %s retry count exceeded maximum' %
                              breakpoint['id'])
        else:
          # This is very common if multiple instances are sending final update
          # simultaneously.
          native.LogInfo('%s, breakpoint: %s' % (err, breakpoint['id']))
      except socket.error as err:
        if retry_count < self.max_transmit_attempts - 1:
          native.LogInfo(
              'Socket error %d while sending breakpoint %s update: %s' %
              (err.errno, breakpoint['id'], traceback.format_exc()))
          retry_list.append((breakpoint, retry_count + 1))
        else:
          native.LogWarning('Breakpoint %s retry count exceeded maximum' %
                            breakpoint['id'])
          # Socket errors shouldn't persist like this; reconnect.
          reconnect = True
      except BaseException:
        native.LogWarning('Fatal error sending breakpoint %s update: %s' %
                          (breakpoint['id'], traceback.format_exc()))
        reconnect = True

    self._transmission_queue.extend(retry_list)

    if not self._transmission_queue:
      self.update_backoff.Succeeded()
      # Nothing to send, wait until next breakpoint update.
      return (reconnect, None)
    else:
      return (reconnect, self.update_backoff.Failed())

  def _GetDebuggee(self):
    """Builds the debuggee structure."""
    major_version = 'v' + version.__version__.split('.')[0]
    python_version = ''.join(platform.python_version().split('.')[:2])
    agent_version = ('google.com/python%s-gcp/%s' %
                     (python_version, major_version))

    debuggee = {
        'project': self._project_number,
        'description': self._GetDebuggeeDescription(),
        'labels': self._debuggee_labels,
        'agentVersion': agent_version,
        'canaryMode': self._canary_mode,
    }

    source_context = self._ReadAppJsonFile('source-context.json')
    if source_context:
      debuggee['sourceContexts'] = [source_context]

    debuggee['uniquifier'] = self._ComputeUniquifier(debuggee)

    return debuggee

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
      with open(os.path.join(sys.path[0], relative_path), 'r') as f:
        return json.load(f)
    except (IOError, ValueError):
      return None
