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
import sys
import threading
import time
import traceback



import apiclient
from apiclient import discovery  # pylint: disable=unused-import
from backoff import Backoff
import httplib2
import oauth2client
from oauth2client import service_account
from oauth2client.contrib.gce import AppAssertionCredentials

import labels
import cdbg_native as native
import uniquifier_computer
import version

# This module catches all exception. This is safe because it runs in
# a daemon thread (so we are not blocking Ctrl+C). We need to catch all
# the exception because HTTP client is unpredictable as far as every
# exception it can throw.
# pylint: disable=broad-except

# API scope we are requesting when service account authentication is enabled.
_CLOUD_PLATFORM_SCOPE = 'https://www.googleapis.com/auth/cloud-platform'

# Base URL for metadata service. Specific attributes are appended to this URL.
_LOCAL_METADATA_SERVICE_PROJECT_URL = ('http://metadata.google.internal/'
                                       'computeMetadata/v1/project/')

# Set of all known debuggee labels (passed down as flags). The value of
# a map is optional environment variable that can be used to set the flag
# (flags still take precedence).
_DEBUGGEE_LABELS = {
    labels.Debuggee.MODULE: ['GAE_SERVICE', 'GAE_MODULE_NAME'],
    labels.Debuggee.VERSION: ['GAE_VERSION', 'GAE_MODULE_VERSION'],
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


class GcpHubClient(object):
  """Controller API client.

  Registers the debuggee, queries the active breakpoints and sends breakpoint
  updates to the backend.

  This class supports two types of authentication: metadata service and service
  account. The mode is selected by calling EnableServiceAccountAuth or
  EnableGceAuth method.

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
    self._wait_token = 'init'
    self._breakpoints = []
    self._main_thread = None
    self._transmission_thread = None
    self._transmission_thread_startup_lock = threading.Lock()
    self._transmission_queue = deque(maxlen=100)
    self._new_updates = threading.Event(False)

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
    discovery.logger.addFilter(self._log_filter)

    #
    # Configuration options (constants only modified by unit test)
    #

    # Delay before retrying failed request.
    self.register_backoff = Backoff()  # Register debuggee.
    self.list_backoff = Backoff()  # Query active breakpoints.
    self.update_backoff = Backoff()  # Update breakpoint.

    # Maximum number of times that the message is re-transmitted before it
    # is assumed to be poisonous and discarded
    self.max_transmit_attempts = 10

  def InitializeDebuggeeLabels(self, flags):
    """Initialize debuggee labels from environment variables and flags.

    The caller passes all the flags that the the debuglet got. This function
    will only use the flags used to label the debuggee. Flags take precedence
    over environment variables.

    Debuggee description is formatted from available flags.

    Project ID is not set here. It is obtained from metadata service or
    specified as a parameter to EnableServiceAccountAuth.

    Args:
      flags: dictionary of debuglet command line flags.
    """
    self._debuggee_labels = {}

    for (label, var_names) in _DEBUGGEE_LABELS.iteritems():
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

    if flags:
      self._debuggee_labels.update(
          {name: value for (name, value) in flags.iteritems()
           if name in _DEBUGGEE_LABELS})

    self._debuggee_labels['projectid'] = self._project_id()

  def EnableServiceAccountAuthP12(self, project_id, project_number,
                                  email, p12_file):
    """Selects service account authentication with a p12 file.

      Using this function is not recommended. Use EnableServiceAccountAuthJson
      for authentication, instead. The p12 file format is no longer recommended.
    Args:
      project_id: GCP project ID (e.g. myproject).
      project_number: numberic GCP project ID (e.g. 72386324623).
      email: service account identifier for use with p12_file
        (...@developer.gserviceaccount.com).
      p12_file: (deprecated) path to an old-style p12 file with the
        private key.
    Raises:
      NotImplementedError indicates that the installed version of oauth2client
      does not support using a p12 file.
    """
    try:
      with open(p12_file, 'rb') as f:
        self._credentials = oauth2client.client.SignedJwtAssertionCredentials(
            email, f.read(), scope=_CLOUD_PLATFORM_SCOPE)
    except AttributeError:
      raise NotImplementedError(
          'P12 key files are no longer supported. Please use a JSON '
          'credentials file instead.')
    self._project_id = lambda: project_id
    self._project_number = lambda: project_number

  def EnableServiceAccountAuthJson(self, project_id, project_number,
                                   auth_json_file):
    """Selects service account authentication using Json credentials.

    Args:
      project_id: GCP project ID (e.g. myproject).
      project_number: numberic GCP project ID (e.g. 72386324623).
      auth_json_file: the JSON keyfile
    """
    self._credentials = (
        service_account.ServiceAccountCredentials
        .from_json_keyfile_name(auth_json_file, scopes=_CLOUD_PLATFORM_SCOPE))
    self._project_id = lambda: project_id
    self._project_number = lambda: project_number

  def EnableGceAuth(self):
    """Selects to use local metadata service for authentication.

    The project ID and project number are also retrieved from the metadata
    service. It is done lazily from the worker thread. The motivation is to
    speed up initialization and be able to recover from failures.
    """
    self._credentials = AppAssertionCredentials()
    self._project_id = lambda: self._QueryGcpProject('project-id')
    self._project_number = lambda: self._QueryGcpProject('numeric-project-id')

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
    http = self._credentials.authorize(http)

    api = apiclient.discovery.build('clouddebugger', 'v2', http=http)
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

        self._debuggee_id = response['debuggee']['id']
        native.LogInfo('Debuggee registered successfully, ID: %s' % (
            self._debuggee_id))
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
          debuggeeId=self._debuggee_id, waitToken=self._wait_token,
          successOnTimeout=True).execute()
      breakpoints = response.get('breakpoints') or []
      self._wait_token = response.get('nextWaitToken')
      if cmp(self._breakpoints, breakpoints) != 0:
        self._breakpoints = breakpoints
        native.LogInfo(
            'Breakpoints list changed, %d active, wait token: %s' % (
                len(self._breakpoints), self._wait_token))
        self.on_active_breakpoints_changed(copy.deepcopy(self._breakpoints))
    except Exception as e:
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
            debuggeeId=self._debuggee_id, id=breakpoint['id'],
            body={'breakpoint': breakpoint}).execute()

        native.LogInfo('Breakpoint %s update transmitted successfully' % (
            breakpoint['id']))
      except apiclient.errors.HttpError as err:
        # Treat 400 error codes (except timeout) as application error that will
        # not be retried. All other errors are assumed to be transient.
        status = err.resp.status
        is_transient = ((status >= 500) or (status == 408))
        if is_transient and retry_count < self.max_transmit_attempts - 1:
          native.LogInfo('Failed to send breakpoint %s update: %s' % (
              breakpoint['id'], traceback.format_exc()))
          retry_list.append((breakpoint, retry_count + 1))
        elif is_transient:
          native.LogWarning(
              'Breakpoint %s retry count exceeded maximum' % breakpoint['id'])
        else:
          # This is very common if multiple instances are sending final update
          # simultaneously.
          native.LogInfo('%s, breakpoint: %s' % (err, breakpoint['id']))
      except Exception:
        native.LogWarning(
            'Fatal error sending breakpoint %s update: %s' % (
                breakpoint['id'], traceback.format_exc()))
        reconnect = True

    self._transmission_queue.extend(retry_list)

    if not self._transmission_queue:
      self.update_backoff.Succeeded()
      # Nothing to send, wait until next breakpoint update.
      return (reconnect, None)
    else:
      return (reconnect, self.update_backoff.Failed())

  def _QueryGcpProject(self, resource):
    """Queries project resource on a local metadata service."""
    url = _LOCAL_METADATA_SERVICE_PROJECT_URL + resource
    http = httplib2.Http()
    response, content = http.request(
        url, headers={'Metadata-Flavor': 'Google'})
    if response['status'] != '200':
      raise RuntimeError(
          'HTTP error %s %s when querying local metadata service at %s' %
          (response['status'], content, url))

    return content

  def _GetDebuggee(self):
    """Builds the debuggee structure."""
    major_version = version.__version__.split('.')[0]

    debuggee = {
        'project': self._project_number(),
        'description': self._GetDebuggeeDescription(),
        'labels': self._debuggee_labels,
        'agentVersion': 'google.com/python2.7-' + major_version
    }

    source_context = self._ReadAppJsonFile('source-context.json')
    if source_context:
      debuggee['sourceContexts'] = [source_context]

    source_contexts = self._ReadAppJsonFile('source-contexts.json')
    if source_contexts:
      debuggee['extSourceContexts'] = source_contexts
    elif source_context:
      debuggee['extSourceContexts'] = [{'context': source_context}]

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

    # Project information.
    uniquifier.update(self._project_id())
    uniquifier.update(self._project_number())

    # Debuggee information.
    uniquifier.update(str(debuggee))

    # Compute hash of application files if we don't have source context. This
    # way we can still distinguish between different deployments.
    if ('minorversion' not in debuggee.get('labels', []) and
        'sourceContexts' not in debuggee and
        'extSourceContexts' not in debuggee):
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
