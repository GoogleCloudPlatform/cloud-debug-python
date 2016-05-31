# Python Cloud Debugger

Google [Cloud Debugger](https://cloud.google.com/tools/cloud-debugger/) for
Python 2.7.

## Overview

The Cloud Debugger lets you inspect the state of an application at any code
location without stopping or slowing it down. The debugger makes it easier to
view the application state without adding logging statements.

You can use the Cloud Debugger on both production and staging instances of your
application. The debugger never pauses the application for more than a few
milliseconds. In most cases, this is not noticeable by users. The Cloud Debugger
gives a read-only experience. Application variables can't be changed through the
debugger.

The Cloud Debugger attaches to all instances of the application. The call stack
and the variables come from the first instance to take the snapshot.

The Python Cloud Debugger is only supported on Linux at the moment. It was tested
on Debian Linux, but it should work on other distributions as well.

The Cloud Debugger consists of 3 primary components:

1.  The debugger agent. This repo implements one for Python 2.7.
2.  Cloud Debugger backend that stores the list of snapshots for each debuggee.
    You can explore the API using the
    [APIs Explorer](https://developers.google.com/apis-explorer/#p/clouddebugger/v2/).
3.  User interface for the debugger implemented using the Cloud Debugger API.
    Currently the only option for Python is the
    [Google Developers Console](https://console.developers.google.com). The
    UI requires that the source code is submitted to
    [Google Cloud Repo](https://cloud.google.com/tools/repo/cloud-repositories/).
    More options (including browsing local source files) are coming soon.

This document only focuses on the Python debugger agent. Please see the
this [page](https://cloud.google.com/tools/cloud-debugger/debugging) for
explanation how to debug an application with the Cloud Debugger.

## Options for Getting Help

1.  StackOverflow: http://stackoverflow.com/questions/tagged/google-cloud-debugger
2.  Google Group: cdbg-feedback@google.com

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

You can also build the agent from source code (OS dependencies are listed in
[build.sh](https://github.com/GoogleCloudPlatform/cloud-debug-python/blob/master/src/build.sh)
script):

```shell
git clone https://github.com/GoogleCloudPlatform/cloud-debug-python.git
cd cloud-debug-python/src/
./build.sh
easy_install dist/google_python_cloud_debugger-*.egg
```

## Setup

### Google Compute Engine

1.  First, make sure that you created the VM with this option enabled:

    > Allow API access to all Google Cloud services in the same project.

    This option lets the debugger agent authenticate with the machine account
    of the Virtual Machine.

    It is possible to use Python Cloud Debugger without it. Please see the
    [next section](#Service_Account) for details.

1.  Install the debugger agent as explained in the [Installation](#Installation)
    section.

2.  Enable the debugger in your application using one of the two options:

    _Option A_: add this code to the beginning of your `main()` function:

    ```python
    # Attach Python Cloud Debugger
    try:
      import googleclouddebugger
      googleclouddebugger.AttachDebugger()
    except ImportError:
      pass
    ```

    _Option B_: run the debugger agent as a module:

    <pre>
    python <b>-m googleclouddebugger --</b> myapp.py
    </pre>

### Service Account

Service account authentication lets you run the debugger agent on any Linux
machine, including outside of [Google Cloud Platform](https://cloud.google.com).
The debugger agent authenticates against the backend with the service account
created in [Google Developers Console](https://console.developers.google.com).
If your application runs on Google Compute Engine,
[metadata service authentication](#Google_Compute_Engine) is an easier option.

The first step for this setup is to create the service account in .p12 format.
Please see this [page](https://cloud.google.com/storage/docs/authentication?hl=en#generating-a-private-key)
for detailed instructions. If you don't have a Google Cloud Platform project,
you can create one for free on [Google Developers Console](https://console.developers.google.com).

Once you have the service account, please note the service account e-mail,
[project ID and project number](https://developers.google.com/console/help/new/#projectnumber).
Then copy the .p12 file to all the machines that run your application.

Then, enable the debugger agent in a similary way as described in
the [previous](#Google_Compute_Engine) section:

_Option A_: add this code to the beginning of your `main()` function:

```python
# Attach Python Cloud Debugger
try:
  import googleclouddebugger
  googleclouddebugger.AttachDebugger(
      enable_service_account_auth=True,
      project_id='my-gcp-project-id',
      project_number='123456789',
      service_account_email='123@developer.gserviceaccount.com',
      service_account_p12_file='/opt/cdbg/gcp.p12')
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
    --service_account_email=<i>123@developer.gserviceaccount.com</i> \
    --service_account_p12_file=<i>/opt/cdbg/gcp.p12</i> \
    --</b> \
    myapp.py
</pre>
