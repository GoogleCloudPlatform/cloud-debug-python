# Python Cloud Debugger Agent

Google [Cloud Debugger](https://cloud.google.com/debugger/) for
Python 3.6, Python 3.7, Python 3.8 and Python 3.9.

## Overview

Cloud Debugger (also known as Stackdriver Debugger) lets you inspect the state
of a running cloud application, at any code location, without stopping or
slowing it down. It is not your traditional process debugger but rather an
always on, whole app debugger taking snapshots from any instance of the app.

Cloud Debugger is safe for use with production apps or during development. The
Python debugger agent only few milliseconds to the request latency when a debug
snapshot is captured. In most cases, this is not noticeable to users.
Furthermore, the Python debugger agent does not allow modification of
application state in any way, and has close to zero impact on the app instances.

Cloud Debugger attaches to all instances of the app providing the ability to
take debug snapshots and add logpoints. A snapshot captures the call-stack and
variables from any one instance that executes the snapshot location. A logpoint
writes a formatted message to the application log whenever any instance of the
app executes the logpoint location.

The Python debugger agent is only supported on Linux at the moment. It was
tested on Debian Linux, but it should work on other distributions as well.

Cloud Debugger consists of 3 primary components:

1.  The Python debugger agent (this repo implements one for CPython 3.6,
    3.7, 3.8 and 3.9).
2.  Cloud Debugger service storing and managing snapshots/logpoints. Explore the
    APIs using
    [APIs Explorer](https://cloud.google.com/debugger/api/reference/rest/).
3.  User interface, including a command line interface
    [`gcloud debug`](https://cloud.google.com/sdk/gcloud/reference/debug/) and a
    Web interface on
    [Google Cloud Console](https://console.cloud.google.com/debug/). See the
    [online help](https://cloud.google.com/debugger/docs/using/snapshots) on how
    to use Google Cloud Console Debug page.

## Getting Help

1.  StackOverflow:
    http://stackoverflow.com/questions/tagged/google-cloud-debugger
2.  Send email to: [Cloud Debugger Feedback](mailto:cdbg-feedback@google.com)
3.  Send Feedback from Google Cloud Console

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

TODO: Figure out what the new dependencies are.  May be able to trim.

```shell
sudo apt-get -y -q --no-install-recommends install \
    curl ca-certificates gcc build-essential cmake \
    python3 python3-dev python3-setuptools python3-pip
```

If the desired target version of Python is not the default version of
the 'python' command on your system, run the build script as `PYTHON=python3.x
./build.sh`.

### Alpine Linux

The Python agent is not regularly tested on Alpine Linux, and support will be on
a best effort basis. The [Dockerfile](alpine/Dockerfile) shows how to build a
minimal image with the agent installed.

## Setup

### Google Cloud Platform

1.  First, make sure that you created the VM with this option enabled:

    > Allow API access to all Google Cloud services in the same project.

    This option lets the Python debugger agent authenticate with the machine
    account of the Virtual Machine.

    It is possible to use the Python debugger agent without it. Please see the
    [next section](#outside-google-cloud-platform) for details.

2.  Install the Python debugger agent as explained in the
    [Installation](#installation) section.

3.  Enable the debugger in your application using one of the two options:

    _Option A_: add this code to the beginning of your `main()` function:

    ```python
    # Attach Python Cloud Debugger
    try:
      import googleclouddebugger
      googleclouddebugger.enable(module='[MODULE]', version='[VERSION]')
    except ImportError:
      pass
    ```

    _Option B_: run the debugger agent as a module:

    <pre>
    python \
        <b>-m googleclouddebugger --module=[MODULE] --version=[VERSION] --</b> \
        myapp.py
    </pre>

    **Note:** This option does not work well with tools such as
    `multiprocessing` or `gunicorn`. These tools spawn workers in separate
    processes, but the debugger does not get enabled on these worker processes.
    Please use _Option A_ instead.

    Where, in both cases:

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
    service account must have at least the `Stackdriver Debugger Agent` role.

2.  Once you have the service account credentials JSON file, deploy it alongside
    the Python debugger agent.

3.  Set the `GOOGLE_APPLICATION_CREDENTIALS` environment variable.

    ```shell
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json
    ```

    Alternatively, you can provide the path to the credentials file directly to
    the debugger agent.

    _Option A_:

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

    _Option B_:

    <pre>
    python \
        <b>-m googleclouddebugger \
        --module=[MODULE] \
        --version=[VERSION] \
        --service_account_json_file=<i>/path/to/credentials.json</i> \
        --</b> \
        myapp.py
    </pre>

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

`breakpoint_enable_canary`: Whether to enable the
[breakpoint canary feature](https://cloud.google.com/debugger/docs/using/snapshots#with_canarying).
It expects a boolean value (`True`/`False`) or a string, with `'True'`
interpreted as `True` and any other string interpreted as `False`). If not
provided, the breakpoint canarying will not be enabled.
