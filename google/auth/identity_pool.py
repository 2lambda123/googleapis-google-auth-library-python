# Copyright 2020 Google LLC
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

"""Identity Pool Credentials.

This module provides credentials to access Google Cloud resources from on-prem
or non-Google Cloud platforms which support external credentials (e.g. OIDC ID
tokens) retrieved from local file locations or local servers. This includes
Microsoft Azure and OIDC identity providers (e.g. K8s workloads registered with
Hub with Hub workload identity enabled).

These credentials are recommended over the use of service account credentials
in on-prem/non-Google Cloud platforms as they do not involve the management of
long-live service account private keys.

Identity Pool Credentials are initialized using external_account
arguments which are typically loaded from an external credentials file or
an external credentials URL. Unlike other Credentials that can be initialized
with a list of explicit arguments, secrets or credentials, external account
clients use the environment and hints/guidelines provided by the
external_account JSON file to retrieve credentials and exchange them for Google
access tokens.
"""

try:
    from collections.abc import Mapping
# Python 2.7 compatibility
except ImportError:  # pragma: NO COVER
    from collections import Mapping
import abc
import io
import json
import os

from google.auth import _helpers
from google.auth import exceptions
from google.auth import external_account

class SubjectTokenSupplier(metaclass=abc.ABCMeta):
    """Base class for subject token suppliers. This can be implemented with custom logic to retrieve
    a subject token to exchange for a Google Cloud access token. The identity pool credential does
    not cache the subject token, so caching logic should be added in the implementation.
    """

    @abc.abstractmethod
    def get_subject_token(self, context, request):
        """Returns the requested subject token. The subject token must be valid. This is not cached by the calling
        Google credential, so caching logic should be implemented in the supplier.

        Args:
            context (google.auth.externalaccount.SupplierContext): The context object
                containing information about the requested audience and subject token type.
            request (google.auth.transport.Request): The object used to make
                HTTP requests.

        Raises:
            google.auth.exceptions.RefreshError: If an error is encountered during
                subject token retrieval logic.

        Returns:
            str: The requested subject token string.
        """
        raise NotImplementedError("")

class _FileSupplier(SubjectTokenSupplier):
    """ Internal implementation of subject token supplier which supports reading a subject token from a file."""

    def __init__(
        self,
        path,
        format_type,
        subject_token_field_name
    ):
        self._path = path
        self._format_type = format_type
        self._subject_token_field_name = subject_token_field_name

    @_helpers.copy_docstring(SubjectTokenSupplier)
    def get_subject_token(self, context, request):
        if not os.path.exists(self._path):
            raise exceptions.RefreshError("File '{}' was not found.".format(self._path))

        with io.open(self._path, "r", encoding="utf-8") as file_obj:
            token_content = file_obj.read(), self._path

        return _parse_token_data(token_content, self._format_type, self._subject_token_field_name)

class _UrlSupplier(SubjectTokenSupplier):
    """ Internal implementation of subject token supplier which supports retrieving a subject token by calling a URL endpoint."""

    def __init__(
        self,
        url,
        format_type,
        subject_token_field_name,
        headers
    ):
        self._url = url
        self._format_type = format_type
        self._subject_token_field_name = subject_token_field_name
        self._headers = headers

    @_helpers.copy_docstring(SubjectTokenSupplier)
    def get_subject_token(self, context, request):
        response = request(url=self._url, method="GET", headers=self._headers)

        # support both string and bytes type response.data
        response_body = (
            response.data.decode("utf-8")
            if hasattr(response.data, "decode")
            else response.data
        )

        if response.status != 200:
            raise exceptions.RefreshError(
                "Unable to retrieve Identity Pool subject token", response_body
            )
        token_content = response_body, self._url
        return _parse_token_data(token_content, self._format_type, self._subject_token_field_name)


def _parse_token_data(
        token_content, format_type="text", subject_token_field_name=None
    ):
        content, filename = token_content
        if format_type == "text":
            token = content
        else:
            try:
                # Parse file content as JSON.
                response_data = json.loads(content)
                # Get the subject_token.
                token = response_data[subject_token_field_name]
            except (KeyError, ValueError):
                raise exceptions.RefreshError(
                    "Unable to parse subject_token from JSON file '{}' using key '{}'".format(
                        filename, subject_token_field_name
                    )
                )
        if not token:
            raise exceptions.RefreshError(
                "Missing subject_token in the credential_source file"
            )
        return token


class Credentials(external_account.Credentials):
    """External account credentials sourced from files and URLs."""

    def __init__(
        self,
        audience,
        subject_token_type,
        token_url,
        credential_source,
        *args,
        **kwargs
    ):
        """Instantiates an external account credentials object from a file/URL.

        Args:
            audience (str): The STS audience field.
            subject_token_type (str): The subject token type.
            token_url (str): The STS endpoint URL.
            credential_source (Mapping): The credential source dictionary used to
                provide instructions on how to retrieve external credential to be
                exchanged for Google access tokens.

                Example credential_source for url-sourced credential::

                    {
                        "url": "http://www.example.com",
                        "format": {
                            "type": "json",
                            "subject_token_field_name": "access_token",
                        },
                        "headers": {"foo": "bar"},
                    }

                Example credential_source for file-sourced credential::

                    {
                        "file": "/path/to/token/file.txt"
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

        super(Credentials, self).__init__(
            audience=audience,
            subject_token_type=subject_token_type,
            token_url=token_url,
            credential_source=credential_source,
            *args,
            **kwargs
        )
        if not isinstance(credential_source, Mapping):
            self._credential_source_file = None
            self._credential_source_url = None
        else:
            self._credential_source_file = credential_source.get("file")
            self._credential_source_url = credential_source.get("url")
            self._credential_source_headers = credential_source.get("headers")
            credential_source_format = credential_source.get("format", {})
            # Get credential_source format type. When not provided, this
            # defaults to text.
            self._credential_source_format_type = (
                credential_source_format.get("type") or "text"
            )
            # environment_id is only supported in AWS or dedicated future external
            # account credentials.
            if "environment_id" in credential_source:
                raise exceptions.MalformedError(
                    "Invalid Identity Pool credential_source field 'environment_id'"
                )
            if self._credential_source_format_type not in ["text", "json"]:
                raise exceptions.MalformedError(
                    "Invalid credential_source format '{}'".format(
                        self._credential_source_format_type
                    )
                )
            # For JSON types, get the required subject_token field name.
            if self._credential_source_format_type == "json":
                self._credential_source_field_name = credential_source_format.get(
                    "subject_token_field_name"
                )
                if self._credential_source_field_name is None:
                    raise exceptions.MalformedError(
                        "Missing subject_token_field_name for JSON credential_source format"
                    )
            else:
                self._credential_source_field_name = None

        if self._credential_source_file and self._credential_source_url:
            raise exceptions.MalformedError(
                "Ambiguous credential_source. 'file' is mutually exclusive with 'url'."
            )
        if not self._credential_source_file and not self._credential_source_url:
            raise exceptions.MalformedError(
                "Missing credential_source. A 'file' or 'url' must be provided."
            )

        if (self._credential_source_file):
            self._subject_token_supplier = _FileSupplier(self._credential_source_file, self._credential_source_format_type, self._credential_source_field_name)
        else:
            self._subject_token_supplier = _UrlSupplier(self._credential_source_url, self._credential_source_format_type, self._credential_source_field_name, self._credential_source_headers)

    @_helpers.copy_docstring(external_account.Credentials)
    def retrieve_subject_token(self, request):
        return self._subject_token_supplier.get_subject_token(self._supplier_context, request)

    def _create_default_metrics_options(self):
        metrics_options = super(Credentials, self)._create_default_metrics_options()
        # Check that credential source is a dict before checking for file vs url. This check needs to be done
        # here because the external_account credential constructor needs to pass the metrics options to the
        # impersonated credential object before the identity_pool credentials are validated.
        if isinstance(self._credential_source, Mapping):
            if self._credential_source.get("file"):
                metrics_options["source"] = "file"
            else:
                metrics_options["source"] = "url"
        return metrics_options

    @classmethod
    def from_info(cls, info, **kwargs):
        """Creates an Identity Pool Credentials instance from parsed external account info.

        Args:
            info (Mapping[str, str]): The Identity Pool external account info in Google
                format.
            kwargs: Additional arguments to pass to the constructor.

        Returns:
            google.auth.identity_pool.Credentials: The constructed
                credentials.

        Raises:
            ValueError: For invalid parameters.
        """
        return super(Credentials, cls).from_info(info, **kwargs)

    @classmethod
    def from_file(cls, filename, **kwargs):
        """Creates an IdentityPool Credentials instance from an external account json file.

        Args:
            filename (str): The path to the IdentityPool external account json file.
            kwargs: Additional arguments to pass to the constructor.

        Returns:
            google.auth.identity_pool.Credentials: The constructed
                credentials.
        """
        return super(Credentials, cls).from_file(filename, **kwargs)
