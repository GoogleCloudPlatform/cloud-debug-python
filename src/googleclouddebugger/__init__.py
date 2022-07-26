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
"""Main module for Python Cloud Debugger.

The debugger is enabled in a very similar way to enabling pdb.

The debugger becomes the main module. It eats up its arguments until it gets
to argument '--' that serves as a separator between debugger arguments and
the application command line. It then attaches the debugger and runs the
actual app.
"""

import logging
import os
import sys

from . import appengine_pretty_printers
from . import breakpoints_manager
from . import collector
from . import error_data_visibility_policy
from . import gcp_hub_client
from . import firebase_client
from . import glob_data_visibility_policy
from . import yaml_data_visibility_config_reader
from . import cdbg_native
from . import version

__version__ = version.__version__

_flags = None
_backend_client = None
_breakpoints_manager = None


def _StartDebugger():
  """Configures and starts the debugger."""
  global _backend_client
  global _breakpoints_manager

  cdbg_native.InitializeModule(_flags)
  cdbg_native.LogInfo(
      f'Initializing Cloud Debugger Python agent version: {__version__}')

  use_firebase = _flags.get('use_firebase')
  if use_firebase:
    _backend_client = firebase_client.FirebaseClient()
    _backend_client.SetupAuth(
        _flags.get('project_id'), _flags.get('service_account_json_file'),
        _flags.get('firebase_db_url'))
  else:
    _backend_client = gcp_hub_client.GcpHubClient()
    _backend_client.SetupAuth(
        _flags.get('project_id'), _flags.get('project_number'),
        _flags.get('service_account_json_file'))
    _backend_client.SetupCanaryMode(
        _flags.get('breakpoint_enable_canary'),
        _flags.get('breakpoint_allow_canary_override'))

  visibility_policy = _GetVisibilityPolicy()

  _breakpoints_manager = breakpoints_manager.BreakpointsManager(
      _backend_client, visibility_policy)

  # Set up loggers for logpoints.
  collector.SetLogger(logging.getLogger())

  collector.CaptureCollector.pretty_printers.append(
      appengine_pretty_printers.PrettyPrinter)

  _backend_client.on_active_breakpoints_changed = (
      _breakpoints_manager.SetActiveBreakpoints)
  _backend_client.on_idle = _breakpoints_manager.CheckBreakpointsExpiration

  _backend_client.InitializeDebuggeeLabels(_flags)
  _backend_client.Start()


def _GetVisibilityPolicy():
  """If a debugger configuration is found, create a visibility policy."""
  try:
    visibility_config = yaml_data_visibility_config_reader.OpenAndRead()
  except yaml_data_visibility_config_reader.Error as err:
    return error_data_visibility_policy.ErrorDataVisibilityPolicy(
        f'Could not process debugger config: {err}')

  if visibility_config:
    return glob_data_visibility_policy.GlobDataVisibilityPolicy(
        visibility_config.blacklist_patterns,
        visibility_config.whitelist_patterns)

  return None


def _DebuggerMain():
  """Starts the debugger and runs the application with debugger attached."""
  global _flags

  # The first argument is cdbg module, which we don't care.
  del sys.argv[0]

  # Parse debugger flags until we encounter '--'.
  _flags = {}
  while sys.argv[0]:
    arg = sys.argv[0]
    del sys.argv[0]

    if arg == '--':
      break

    (name, value) = arg.strip('-').split('=', 2)
    _flags[name] = value

  _StartDebugger()

  # Run the app. The following code was mostly copied from pdb.py.
  app_path = sys.argv[0]

  sys.path[0] = os.path.dirname(app_path)

  import __main__  # pylint: disable=import-outside-toplevel
  __main__.__dict__.clear()
  __main__.__dict__.update({
      '__name__': '__main__',
      '__file__': app_path,
      '__builtins__': __builtins__
  })
  locals = globals = __main__.__dict__  # pylint: disable=redefined-builtin

  sys.modules['__main__'] = __main__

  with open(app_path, encoding='utf-8') as f:
    code = compile(f.read(), app_path, 'exec')
    exec(code, globals, locals)  # pylint: disable=exec-used


# pylint: disable=invalid-name
def enable(**kwargs):
  """Starts the debugger for already running application.

  This function should only be called once.

  Args:
    **kwargs: debugger configuration flags.

  Raises:
    RuntimeError: if called more than once.
    ValueError: if flags is not a valid dictionary.
  """
  global _flags

  if _flags is not None:
    raise RuntimeError('Debugger already attached')

  _flags = kwargs
  _StartDebugger()


# AttachDebugger is an alias for enable, preserved for compatibility.
AttachDebugger = enable
