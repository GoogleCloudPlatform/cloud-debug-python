# Python Cloud Debugger Agent

Google [Cloud Debugger](https://cloud.google.com/debugger/) for Python 2.7.

## Overview

Cloud Debugger (also known as Stackdriver Debugger) lets you inspect the state
of a running cloud application, at any code location, without stopping or
slowing it down. It is not your traditional process debugger but rather an
always on, whole app debugger taking snapshots from any instance of the app.

Cloud Debugger is safe for use with production apps or during development.
The Python debugger agent only few milliseconds to the request latency when a
debug snapshot is captured. In most cases, this is not noticeable to users.
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

1.  The Python debugger agent (this repo implements one for Python 2.7).
2.  Cloud Debugger service storing and managing snapshots/logpoints.
    Explore the API's using
    [APIs Explorer](https://developers.google.com/apis-explorer/#p/clouddebugger/v2/).
3.  User interface, including a command line interface
    [`gcloud debug`](https://cloud.google.com/sdk/gcloud/reference/debug/) and a
    Web interface on
    [Google Cloud Console](https://console.developers.google.com/debug/).
    See the [online help](https://cloud.google.com/debugger/docs/debugging) on
    how to use Google Cloud Console Debug page.

## Getting Help

1.  StackOverflow: http://stackoverflow.com/questions/tagged/google-cloud-debugger
2.  Send email to: [Cloud Debugger Feedback](mailto:cdbg-feedback@google.com)
3.  Send Feedback from Google Cloud Console

## Installation

The easiest way to install the Python Cloud Debugger is with PyPI:

```shell
pip install google-python-cloud-debugger
```

Alternatively, download the *egg* package from
[Releases](https://github.com/GoogleCloudPlatform/cloud-debug-python/releases)
and install the debugger agent with:

```shell
easy_install google_python_cloud_debugger-py2.7-linux-x86_64.egg
```

You can also build the agent from source code:

```shell
git clone https://github.com/GoogleCloudPlatform/cloud-debug-python.git
cd cloud-debug-python/src/
./build.sh
easy_install dist/google_python_cloud_debugger-*.egg
```

Note that the build script assumes some dependencies. To install these
dependencies on Debian, run this command:

```shell
sudo apt-get -y -q --no-install-recommends install \
    curl ca-certificates gcc build-essential cmake \
    python python-dev libpython2.7 python-setuptools
```

## Setup

### Google Cloud Platform

1.  First, make sure that you created the VM with this option enabled:

    > Allow API access to all Google Cloud services in the same project.

    This option lets the Python debugger agent authenticate with the machine
    account of the Virtual Machine.

    It is possible to use the Python debugger agent without it. Please see the
    [next section](#Service_Account) for details.

1.  Install the Python debugger agent as explained in the
    [Installation](#Installation) section.

2.  Enable the debugger in your application using one of the two options:

    _Option A_: add this code to the beginning of your `main()` function:

    ```python
    # Attach Python Cloud Debugger
    try:
      import googleclouddebugger
      googleclouddebugger.enable()
    except ImportError:
      pass
    ```

    _Option B_: run the debugger agent as a module:

    <pre>
    python <b>-m googleclouddebugger --</b> myapp.py
    </pre>


### Service Account

To use the Python debugger agent on machines <i>not</i> hosted by Google Cloud
Platform, the agent must use a Google Cloud Platform service-account credentials
to authenticate with the Cloud Debugger Service.

Use the Google Cloud Console Service Accounts
[page](https://console.cloud.google.com/iam-admin/serviceaccounts/project) to
create a credentials file for an existing or new service-account. The
service-account must have at least the `Cloud Debugger Agent` role to be
accepted by the Cloud Debugger Service.
If you don't have a Google Cloud Platform project, you can create one for free
on [Google Cloud Console](https://console.cloud.google.com).

Once you have the service-account JSON file, deploy it alongside the Python
debugger agent.

Once you have the service account, please note the service account e-mail,
project ID and project number.

Then, enable the debugger agent using one of these two options:

_Option A_: add this code to the beginning of your `main()` function:

```python
# Attach Python Cloud Debugger
try:
  import googleclouddebugger
  googleclouddebugger.enable(
      enable_service_account_auth=True,
      project_id='my-gcp-project-id',
      project_number='123456789',
      service_account_json_file='/opt/cdbg/gcp-svc.json')
except ImportError:
  pass
```

_Option B_: run the debugger agent as a module:

<pre>
python \
    <b>-m googleclouddebugger \
    --enable_service_account_auth=1 \
    --project_id=<i>my-gcp-project-id</i> \
    --project_number=<i>123456789</i> \
    --service_account_json_file=<i>/opt/cdbg/gcp-svc.json</i> \
    --</b> \
    myapp.py
</pre>

### Django Web Framework

You can use the Cloud Debugger to debug Django web framework applications.


The best way to enable the Cloud Debugger with Django is to add the following
code fragment to your `manage.py` file:

```python
# Attach the Python Cloud debugger (only the main server process).
if os.environ.get('RUN_MAIN') or '--noreload' in sys.argv:
  try:
    import googleclouddebugger
    googleclouddebugger.enable()
  except ImportError:
    pass
```


Alternatively, you can pass the `--noreload` flag when running the Django
`manage.py` and use any one of the option A and B listed earlier. Note that
using the `--noreload` flag disables the autoreload feature in Django, which
means local changes to files will not be automatically picked up by Django.

