# Python Snapshot Debugger Agent

[Snapshot debugger](https://github.com/GoogleCloudPlatform/snapshot-debugger/)
agent for Python 3.6, Python 3.7, Python 3.8, Python 3.9, and Python 3.10.

## Overview

Snapshot Debugger lets you inspect the state
of a running cloud application, at any code location, without stopping or
slowing it down. It is not your traditional process debugger but rather an
always on, whole app debugger taking snapshots from any instance of the app.

Snapshot Debugger is safe for use with production apps or during development. The
Python debugger agent only few milliseconds to the request latency when a debug
snapshot is captured. In most cases, this is not noticeable to users.
Furthermore, the Python debugger agent does not allow modification of
application state in any way, and has close to zero impact on the app instances.

Snapshot Debugger attaches to all instances of the app providing the ability to
take debug snapshots and add logpoints. A snapshot captures the call-stack and
variables from any one instance that executes the snapshot location. A logpoint
writes a formatted message to the application log whenever any instance of the
app executes the logpoint location.

The Python debugger agent is only supported on Linux at the moment. It was
tested on Debian Linux, but it should work on other distributions as well.

Snapshot Debugger consists of 3 primary components:

1.  The Python debugger agent (this repo implements one for CPython 3.6,
    3.7, 3.8, 3.9, and 3.10).
2.  A Firebase Realtime Database for storing and managing snapshots/logpoints.
    Explore the 
    [schema](https://github.com/GoogleCloudPlatform/snapshot-debugger/blob/main/docs/SCHEMA.md).
3.  User interface, including a command line interface
    [`snapshot-dbg-cli`](https://pypi.org/project/snapshot-dbg-cli/) and a
    [VSCode extension](https://github.com/GoogleCloudPlatform/snapshot-debugger/tree/main/snapshot_dbg_extension)

## Getting Help

1.  File an [issue](https://github.com/GoogleCloudPlatform/cloud-debug-python/issues)
1.  StackOverflow:
    http://stackoverflow.com/questions/tagged/google-cloud-debugger

## Installation

The easiest way to install the Python Cloud Debugger is with PyPI:

```shell
pip install google-python-cloud-debugger
```

You can also build the agent from source code:

```shell
git clone https://github.com/GoogleCloudPlatform/cloud-debug-python.git
cd cloud-debug-python/src/
./build.sh
pip install dist/google_python_cloud_debugger-*.whl
```

Note that the build script assumes some dependencies. To install these
dependencies on Debian, run this command:

```shell
sudo apt-get -y -q --no-install-recommends install \
    curl ca-certificates gcc build-essential cmake \
    python3 python3-dev python3-pip
```

If the desired target version of Python is not the default version of
the 'python3' command on your system, run the build script as `PYTHON=python3.x
./build.sh`.

### Alpine Linux

The Python agent is not regularly tested on Alpine Linux, and support will be on
a best effort basis. The [Dockerfile](alpine/Dockerfile) shows how to build a
minimal image with the agent installed.

## Setup

### Google Cloud Platform

1.  First, make sure that the VM has the
    [required scopes](https://github.com/GoogleCloudPlatform/snapshot-debugger/blob/main/docs/configuration.md#access-scopes).

2.  Install the Python debugger agent as explained in the
    [Installation](#installation) section.

3.  Enable the debugger in your application:

    ```python
    # Attach Python Cloud Debugger
    try:
      import googleclouddebugger
      googleclouddebugger.enable(module='[MODULE]', version='[VERSION]')
    except ImportError:
      pass
    ```

    Where:

    *   `[MODULE]` is the name of your app. This, along with the version, is
        used to identify the debug target in the UI.<br>
        Example values: `MyApp`, `Backend`, or `Frontend`.

    *   `[VERSION]` is the app version (for example, the build ID). The UI
        displays the running version as `[MODULE] - [VERSION]`.<br>
        Example values: `v1.0`, `build_147`, or `v20170714`.

### Outside Google Cloud Platform

To use the Python debugger agent on machines <i>not</i> hosted by Google Cloud
Platform, you must set up credentials to authenticate with Google Cloud APIs. By
default, the debugger agent tries to find the [Application Default
Credentials](https://cloud.google.com/docs/authentication/production) on the
system. This can either be from your personal account or a dedicated service
account.

#### Personal Account

1.  Set up Application Default Credentials through
    [gcloud](https://cloud.google.com/sdk/gcloud/reference/auth/application-default/login).

    ```shell
    gcloud auth application-default login
    ```

2.  Follow the rest of the steps in the [GCP](#google-cloud-platform) section.

#### Service Account

1.  Use the Google Cloud Console Service Accounts
    [page](https://console.cloud.google.com/iam-admin/serviceaccounts/project)
    to create a credentials file for an existing or new service account. The
    service account must have at least the `roles/firebasedatabase.admin` role.

2.  Once you have the service account credentials JSON file, deploy it alongside
    the Python debugger agent.

3.  Set the `GOOGLE_APPLICATION_CREDENTIALS` environment variable.

    ```shell
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json
    ```

    Alternatively, you can provide the path to the credentials file directly to
    the debugger agent.

    ```python
    # Attach Python Cloud Debugger
    try:
      import googleclouddebugger
      googleclouddebugger.enable(
          module='[MODULE]',
          version='[VERSION]',
          service_account_json_file='/path/to/credentials.json')
    except ImportError:
      pass
    ```
4.  Follow the rest of the steps in the [GCP](#google-cloud-platform) section.

### Django Web Framework

You can use the Cloud Debugger to debug Django web framework applications.

The best way to enable the Cloud Debugger with Django is to add the following
code fragment to your `manage.py` file:

```python
# Attach the Python Cloud debugger (only the main server process).
if os.environ.get('RUN_MAIN') or '--noreload' in sys.argv:
  try:
    import googleclouddebugger
    googleclouddebugger.enable(module='[MODULE]', version='[VERSION]')
  except ImportError:
    pass
```

Alternatively, you can pass the `--noreload` flag when running the Django
`manage.py` and use any one of the option A and B listed earlier. Note that
using the `--noreload` flag disables the autoreload feature in Django, which
means local changes to files will not be automatically picked up by Django.

## Historical note

Version 3.x of this agent supported both the now shutdown Cloud Debugger service
(by default) and the
[Snapshot Debugger](https://github.com/GoogleCloudPlatform/snapshot-debugger/)
(Firebase RTDB backend) by setting the `use_firebase` flag to true. Version 4.0
removed support for the Cloud Debugger service, making the Snapshot Debugger the
default.  To note the `use_firebase` flag is now obsolete, but still present for
backward compatibility.

## Flag Reference

The agent offers various flags to configure its behavior. Flags can be specified
as keyword arguments:

```python
googleclouddebugger.enable(flag_name='flag_value')
```

or as command line arguments when running the agent as a module:

```shell
python -m googleclouddebugger --flag_name=flag_value -- myapp.py
```

The following flags are available:

`module`: A name for your app. This, along with the version, is used to identify
the debug target in the UI. <br>
Example values: `MyApp`, `Backend`, or `Frontend`.

`version`: A version for your app. The UI displays the running version as
`[MODULE] - [VERSION]`.<br>
If not provided, the UI will display the generated debuggee ID instead.<br>
Example values: `v1.0`, `build_147`, or `v20170714`.

`service_account_json_file`: Path to JSON credentials of a [service
account](https://cloud.google.com/iam/docs/service-accounts) to use for
authentication. If not provided, the agent will fall back to [Application
Default Credentials](https://cloud.google.com/docs/authentication/production)
which are automatically available on machines hosted on GCP, or can be set via
`gcloud auth application-default login` or the `GOOGLE_APPLICATION_CREDENTIALS`
environment variable.

`firebase_db_url`: Url pointing to a configured Firebase Realtime Database for
the agent to use to store snapshot data.
https://**PROJECT_ID**-cdbg.firebaseio.com will be used if not provided. where
**PROJECT_ID** is your project ID.

## Development

The following instructions are intended to help with modifying the codebase.

### Testing

#### Unit tests

Run the `build_and_test.sh` script from the root of the repository to build and
run the unit tests using the locally installed version of Python.

Run `bazel test tests/cpp:all` from the root of the repository to run unit
tests against the C++ portion of the codebase.

#### Local development

You may want to run an agent with local changes in an application in order to
validate functionality in a way that unit tests don't fully cover.  To do this,
you will need to build the agent:
```
cd src
./build.sh
cd ..
```

The built agent will be available in the `src/dist` directory.  You can now
force the installation of the agent using:
```
pip3 install src/dist/* --force-reinstall
```

You can now run your test application using the development build of the agent
in whatever way you desire.

It is recommended that you do this within a
[virtual environment](https://docs.python.org/3/library/venv.html).

### Build & Release (for project owners)

Before performing a release, be sure to update the version number in
`src/googleclouddebugger/version.py`.  Tag the commit that increments the
version number (eg. `v3.1`) and create a Github release.

Run the `build-dist.sh` script from the root of the repository to build,
test, and generate the distribution whls.  You may need to use `sudo`
depending on your system's docker setup.

Build artifacts will be placed in `/dist` and can be pushed to pypi by running:
```
twine upload dist/*.whl
```
