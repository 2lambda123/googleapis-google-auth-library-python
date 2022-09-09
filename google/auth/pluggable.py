# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pluggable Credentials.
Pluggable Credentials are initialized using external_account arguments which
are typically loaded from third-party executables. Unlike other
credentials that can be initialized with a list of explicit arguments, secrets
or credentials, external account clients use the environment and hints/guidelines
provided by the external_account JSON file to retrieve credentials and exchange
them for Google access tokens.

Example credential_source for pluggable credential:
{
    "executable": {
        "command": "/path/to/get/credentials.sh --arg1=value1 --arg2=value2",
        "timeout_millis": 5000,
        "output_file": "/path/to/generated/cached/credentials"
    }
}
"""

try:
    from collections.abc import Mapping
# Python 2.7 compatibility
except ImportError:  # pragma: NO COVER
    from collections import Mapping
import json
import os
import subprocess
import sys
import time

from google.auth import _helpers
from google.auth import exceptions
from google.auth import external_account

# The max supported executable spec version.
EXECUTABLE_SUPPORTED_MAX_VERSION = 1

EXECUTABLE_TIMEOUT_MILLIS_DEFAULT = 30 * 1000  # 30 seconds
EXECUTABLE_TIMEOUT_MILLIS_LOWER_BOUND = 5 * 1000  # 5 seconds
EXECUTABLE_TIMEOUT_MILLIS_UPPER_BOUND = 120 * 1000  # 2 minutes

EXECUTABLE_INTERACTIVE_TIMEOUT_MILLIS_DEFAULT = 5 * 60 * 1000  # 5 minutes
EXECUTABLE_INTERACTIVE_TIMEOUT_MILLIS_LOWER_BOUND = 5 * 60 * 1000  # 5 minutes
EXECUTABLE_INTERACTIVE_TIMEOUT_MILLIS_UPPER_BOUND = 30 * 60 * 1000  # 30 minutes


class Credentials(external_account.Credentials):
    """External account credentials sourced from executables."""

    def __init__(
        self,
        audience,
        subject_token_type,
        token_url,
        credential_source,
        *args,
        **kwargs
    ):
        """Instantiates an external account credentials object from a executables.

        Args:
            audience (str): The STS audience field.
            subject_token_type (str): The subject token type.
            token_url (str): The STS endpoint URL.
            credential_source (Mapping): The credential source dictionary used to
                provide instructions on how to retrieve external credential to be
                exchanged for Google access tokens.

                Example credential_source for pluggable credential:

                    {
                        "executable": {
                            "command": "/path/to/get/credentials.sh --arg1=value1 --arg2=value2",
                            "timeout_millis": 5000,
                            "output_file": "/path/to/generated/cached/credentials"
                        }
                    }
            args (List): Optional positional arguments passed into the underlying :meth:`~external_account.Credentials.__init__` method.
            kwargs (Mapping): Optional keyword arguments passed into the underlying :meth:`~external_account.Credentials.__init__` method.

        Raises:
            google.auth.exceptions.RefreshError: If an error is encountered during
                access token retrieval logic.
            ValueError: For invalid parameters.

        .. note:: Typically one of the helper constructors
            :meth:`from_file` or
            :meth:`from_info` are used instead of calling the constructor directly.
        """

        self.interactive = kwargs.pop("interactive", False)
        super(Credentials, self).__init__(
            audience=audience,
            subject_token_type=subject_token_type,
            token_url=token_url,
            credential_source=credential_source,
            *args,
            **kwargs
        )
        if not isinstance(credential_source, Mapping):
            self._credential_source_executable = None
            raise ValueError(
                "Missing credential_source. The credential_source is not a dict."
            )
        self._credential_source_executable = credential_source.get("executable")
        if not self._credential_source_executable:
            raise ValueError(
                "Missing credential_source. An 'executable' must be provided."
            )
        self._credential_source_executable_command = self._credential_source_executable.get(
            "command"
        )
        self._credential_source_executable_timeout_millis = self._credential_source_executable.get(
            "timeout_millis"
        )
        self._credential_source_executable_interactive_timeout_millis = self._credential_source_executable.get(
            "interactive_timeout_millis"
        )
        self._credential_source_executable_output_file = self._credential_source_executable.get(
            "output_file"
        )

        if not self._credential_source_executable_command:
            raise ValueError(
                "Missing command field. Executable command must be provided."
            )
        if not self._credential_source_executable_timeout_millis:
            self._credential_source_executable_timeout_millis = (
                EXECUTABLE_TIMEOUT_MILLIS_DEFAULT
            )
        elif (
            self._credential_source_executable_timeout_millis
            < EXECUTABLE_TIMEOUT_MILLIS_LOWER_BOUND
            or self._credential_source_executable_timeout_millis
            > EXECUTABLE_TIMEOUT_MILLIS_UPPER_BOUND
        ):
            raise ValueError("Timeout must be between 5 and 120 seconds.")

        if not self._credential_source_executable_interactive_timeout_millis:
            self._credential_source_executable_interactive_timeout_millis = (
                EXECUTABLE_INTERACTIVE_TIMEOUT_MILLIS_DEFAULT
            )
        elif (
            self._credential_source_executable_interactive_timeout_millis
            < EXECUTABLE_INTERACTIVE_TIMEOUT_MILLIS_LOWER_BOUND
            or self._credential_source_executable_interactive_timeout_millis
            > EXECUTABLE_INTERACTIVE_TIMEOUT_MILLIS_UPPER_BOUND
        ):
            raise ValueError("Interactive timeout must be between 5 and 30 minutes.")

    @_helpers.copy_docstring(external_account.Credentials)
    def retrieve_subject_token(self, request):
        env_allow_executables = os.environ.get(
            "GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES"
        )
        if env_allow_executables != "1":
            raise ValueError(
                "Executables need to be explicitly allowed (set GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES to '1') to run."
            )
        if self.interactive and not self._credential_source_executable_output_file:
            raise ValueError(
                "An output_file must be specified in the credential configuration for interactive mode."
            )

        if self.interactive and not self.is_workforce_pool:
            raise ValueError("Interactive mode is only enabled for workforce pool.")

        # Check output file.
        if self._credential_source_executable_output_file is not None:
            try:
                with open(
                    self._credential_source_executable_output_file, encoding="utf-8"
                ) as output_file:
                    response = json.load(output_file)
            except Exception:
                pass
            else:
                try:
                    # If the cached response is expired, _parse_subject_token will raise an error which will be ignored and we will call the executable again.
                    subject_token = self._parse_subject_token(response)
                    if (
                        "expiration_time" not in response
                    ):  # Always treat missing expiration_time as expired and proceed to executable run.
                        raise exceptions.RefreshError
                except ValueError:
                    raise
                except exceptions.RefreshError:
                    pass
                else:
                    return subject_token

        if not _helpers.is_python_3():
            raise exceptions.RefreshError(
                "Pluggable auth is only supported for python 3.6+"
            )

        # Inject env vars.
        env = os.environ.copy()
        env["GOOGLE_EXTERNAL_ACCOUNT_AUDIENCE"] = self._audience
        env["GOOGLE_EXTERNAL_ACCOUNT_TOKEN_TYPE"] = self._subject_token_type
        env["GOOGLE_EXTERNAL_ACCOUNT_ID"] = self.service_account_email
        env["GOOGLE_EXTERNAL_ACCOUNT_INTERACTIVE"] = "1" if self.interactive else "0"
        env["GOOGLE_EXTERNAL_ACCOUNT_REVOKE"] = 0

        if self._service_account_impersonation_url is not None:
            env[
                "GOOGLE_EXTERNAL_ACCOUNT_IMPERSONATED_EMAIL"
            ] = self.service_account_email
        if self._credential_source_executable_output_file is not None:
            env[
                "GOOGLE_EXTERNAL_ACCOUNT_OUTPUT_FILE"
            ] = self._credential_source_executable_output_file

        exe_timeout = (
            self._credential_source_executable_interactive_timeout_millis / 1000
            if self.interactive
            else self._credential_source_executable_timeout_millis / 1000
        )
        exe_stdin = sys.stdin if self.interactive else None
        exe_stdout = sys.stdout if self.interactive else subprocess.PIPE
        exe_stderr = sys.stdout if self.interactive else subprocess.STDOUT

        result = subprocess.run(
            self._credential_source_executable_command.split(),
            timeout=exe_timeout,
            stdin=exe_stdin,
            stdout=exe_stdout,
            stderr=exe_stderr,
            env=env,
        )
        if result.returncode != 0:
            raise exceptions.RefreshError(
                "Executable exited with non-zero return code {}. Error: {}".format(
                    result.returncode, result.stdout
                )
            )

        response = (
            json.load(
                open(self._credential_source_executable_output_file, encoding="utf-8")
            )
            if self.interactive
            else json.loads(result.stdout.decode("utf-8"))
        )
        subject_token = self._parse_subject_token(response)
        return subject_token

    def revoke(self, request):
        """Revokes the subject token using the credential_source object.

        Args:
            request (google.auth.transport.Request): A callable used to make
                HTTP requests.
        Raises:
            google.auth.exceptions.RefreshError: If the executable revocation
                not properly executed.

        """
        env_allow_executables = os.environ.get(
            "GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES"
        )
        if env_allow_executables != "1":
            raise ValueError(
                "Executables need to be explicitly allowed (set GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES to '1') to run."
            )

        if not self.interactive:
            raise ValueError("Revoke is only enabled under interactive mode.")

        if not _helpers.is_python_3():
            raise exceptions.RefreshError(
                "Pluggable auth is only supported for python 3.6+"
            )

        # Inject variables
        env = os.environ.copy()
        env["GOOGLE_EXTERNAL_ACCOUNT_AUDIENCE"] = self._audience
        env["GOOGLE_EXTERNAL_ACCOUNT_TOKEN_TYPE"] = self._subject_token_type
        env["GOOGLE_EXTERNAL_ACCOUNT_ID"] = self.service_account_email
        env["GOOGLE_EXTERNAL_ACCOUNT_INTERACTIVE"] = "1"
        env["GOOGLE_EXTERNAL_ACCOUNT_REVOKE"] = "1"
        if self._service_account_impersonation_url is not None:
            env[
                "GOOGLE_EXTERNAL_ACCOUNT_IMPERSONATED_EMAIL"
            ] = self.service_account_email
        env[
            "GOOGLE_EXTERNAL_ACCOUNT_OUTPUT_FILE"
        ] = self._credential_source_executable_output_file

        result = subprocess.run(
            self._credential_source_executable_command.split(),
            timeout=self._credential_source_executable_interactive_timeout_millis
            / 1000,
            stdin=sys.stdin,
            stdout=sys.stdout,
            env=env,
        )

        if result.returncode != 0:
            raise exceptions.RefreshError(
                "Auth revoke failed on executable. Exit with non-zero return code {}".format(
                    result.returncode
                )
            )

        # TODO: clear cache when the in memory cache feature implemented.

    @classmethod
    def from_info(cls, info, **kwargs):
        """Creates a Pluggable Credentials instance from parsed external account info.

        Args:
            info (Mapping[str, str]): The Pluggable external account info in Google
                format.
            kwargs: Additional arguments to pass to the constructor.

        Returns:
            google.auth.pluggable.Credentials: The constructed
                credentials.

        Raises:
            ValueError: For invalid parameters.
        """
        return super(Credentials, cls).from_info(info, **kwargs)

    @classmethod
    def from_file(cls, filename, **kwargs):
        """Creates an Pluggable Credentials instance from an external account json file.

        Args:
            filename (str): The path to the Pluggable external account json file.
            kwargs: Additional arguments to pass to the constructor.

        Returns:
            google.auth.pluggable.Credentials: The constructed
                credentials.
        """
        return super(Credentials, cls).from_file(filename, **kwargs)

    def _parse_subject_token(self, response):
        if "version" not in response:
            raise ValueError("The executable response is missing the version field.")
        if response["version"] > EXECUTABLE_SUPPORTED_MAX_VERSION:
            raise exceptions.RefreshError(
                "Executable returned unsupported version {}.".format(
                    response["version"]
                )
            )
        if "success" not in response:
            raise ValueError("The executable response is missing the success field.")
        if not response["success"]:
            if "code" not in response or "message" not in response:
                raise ValueError(
                    "Error code and message fields are required in the response."
                )
            raise exceptions.RefreshError(
                "Executable returned unsuccessful response: code: {}, message: {}.".format(
                    response["code"], response["message"]
                )
            )
        if (
            "expiration_time" not in response
            and not self.interactive
            and self._credential_source_executable_output_file
        ):
            raise ValueError(
                "The executable response must contain an expiration_time for successful responses when an output_file has been specified in the configuration in non-interactive mode."
            )
        if "expiration_time" in response and response["expiration_time"] < time.time():
            raise exceptions.RefreshError(
                "The token returned by the executable is expired."
            )
        if "token_type" not in response:
            raise ValueError("The executable response is missing the token_type field.")
        if (
            response["token_type"] == "urn:ietf:params:oauth:token-type:jwt"
            or response["token_type"] == "urn:ietf:params:oauth:token-type:id_token"
        ):  # OIDC
            return response["id_token"]
        elif response["token_type"] == "urn:ietf:params:oauth:token-type:saml2":  # SAML
            return response["saml_response"]
        else:
            raise exceptions.RefreshError("Executable returned unsupported token type.")
