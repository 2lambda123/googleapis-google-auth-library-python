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

"""External Account Credentials.

This module provides credentials that exchange workload identity pool external
credentials for Google access tokens. This facilitates accessing Google Cloud
Platform resources from on-prem and non-Google Cloud platforms (e.g. AWS,
Microsoft Azure, OIDC identity providers), using native credentials retrieved
from the current environment without the need to copy, save and manage
long-lived service account credentials.

Specifically, this is intended to use access tokens acquired using the GCP STS
token exchange endpoint following the `OAuth 2.0 Token Exchange`_ spec.

.. _OAuth 2.0 Token Exchange: https://tools.ietf.org/html/rfc8693
"""

import abc
import copy
import datetime
import io
import json
import re

import six
from urllib3.util import parse_url

from google.auth import _helpers
from google.auth import credentials
from google.auth import exceptions
from google.auth import impersonated_credentials
from google.oauth2 import sts
from google.oauth2 import utils

# External account JSON type identifier.
_EXTERNAL_ACCOUNT_JSON_TYPE = "external_account"
# The token exchange grant_type used for exchanging credentials.
_STS_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
# The token exchange requested_token_type. This is always an access_token.
_STS_REQUESTED_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
# Cloud resource manager URL used to retrieve project information.
_CLOUD_RESOURCE_MANAGER = "https://cloudresourcemanager.googleapis.com/v1/projects/"


@six.add_metaclass(abc.ABCMeta)
class Credentials(
    credentials.Scoped,
    credentials.CredentialsWithQuotaProject,
    credentials.CredentialsWithTokenUri,
):
    """Base class for all external account credentials.

    This is used to instantiate Credentials for exchanging external account
    credentials for Google access token and authorizing requests to Google APIs.
    The base class implements the common logic for exchanging external account
    credentials for Google access tokens.
    """

    def __init__(
        self,
        audience,
        subject_token_type,
        token_url,
        credential_source,
        service_account_impersonation_url=None,
        service_account_impersonation_options=None,
        client_id=None,
        client_secret=None,
        quota_project_id=None,
        scopes=None,
        default_scopes=None,
        workforce_pool_user_project=None,
    ):
        """Instantiates an external account credentials object.

        Args:
            audience (str): The STS audience field.
            subject_token_type (str): The subject token type.
            token_url (str): The STS endpoint URL.
            credential_source (Mapping): The credential source dictionary.
            service_account_impersonation_url (Optional[str]): The optional service account
                impersonation generateAccessToken URL.
            client_id (Optional[str]): The optional client ID.
            client_secret (Optional[str]): The optional client secret.
            quota_project_id (Optional[str]): The optional quota project ID.
            scopes (Optional[Sequence[str]]): Optional scopes to request during the
                authorization grant.
            default_scopes (Optional[Sequence[str]]): Default scopes passed by a
                Google client library. Use 'scopes' for user-defined scopes.
            workforce_pool_user_project (Optona[str]): The optional workforce pool user
                project number when the credential corresponds to a workforce pool and not
                a workload identity pool. The underlying principal must still have
                serviceusage.services.use IAM permission to use the project for
                billing/quota.
        Raises:
            google.auth.exceptions.RefreshError: If the generateAccessToken
                endpoint returned an error.
        """
        super(Credentials, self).__init__()
        self._audience = audience
        self._subject_token_type = subject_token_type
        self._token_url = token_url
        self._credential_source = credential_source
        self._service_account_impersonation_url = service_account_impersonation_url
        self._service_account_impersonation_options = (
            service_account_impersonation_options or {}
        )
        self._client_id = client_id
        self._client_secret = client_secret
        self._quota_project_id = quota_project_id
        self._scopes = scopes
        self._default_scopes = default_scopes
        self._workforce_pool_user_project = workforce_pool_user_project

        Credentials.validate_token_url(token_url)
        if service_account_impersonation_url:
            Credentials.validate_service_account_impersonation_url(
                service_account_impersonation_url
            )

        if self._client_id:
            self._client_auth = utils.ClientAuthentication(
                utils.ClientAuthType.basic, self._client_id, self._client_secret
            )
        else:
            self._client_auth = None
        self._sts_client = sts.Client(self._token_url, self._client_auth)

        if self._service_account_impersonation_url:
            self._impersonated_credentials = self._initialize_impersonated_credentials()
        else:
            self._impersonated_credentials = None
        self._project_id = None

        if not self.is_workforce_pool and self._workforce_pool_user_project:
            # Workload identity pools do not support workforce pool user projects.
            raise ValueError(
                "workforce_pool_user_project should not be set for non-workforce pool "
                "credentials"
            )

    @property
    def info(self):
        """Generates the dictionary representation of the current credentials.

        Returns:
            Mapping: The dictionary representation of the credentials. This is the
                reverse of "from_info" defined on the subclasses of this class. It is
                useful for serializing the current credentials so it can deserialized
                later.
        """
        config_info = {
            "type": _EXTERNAL_ACCOUNT_JSON_TYPE,
            "audience": self._audience,
            "subject_token_type": self._subject_token_type,
            "token_url": self._token_url,
            "service_account_impersonation_url": self._service_account_impersonation_url,
            "service_account_impersonation": copy.deepcopy(
                self._service_account_impersonation_options
            )
            or None,
            "credential_source": copy.deepcopy(self._credential_source),
            "quota_project_id": self._quota_project_id,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "workforce_pool_user_project": self._workforce_pool_user_project,
        }
        return {key: value for key, value in config_info.items() if value is not None}

    @property
    def constructor_args(self):
        d = dict(
            audience=self._audience,
            subject_token_type=self._subject_token_type,
            token_url=self._token_url,
            credential_source=self._credential_source,
            service_account_impersonation_url=None,
            service_account_impersonation_options={},
            client_id=self._client_id,
            client_secret=self._client_secret,
            quota_project_id=self._quota_project_id,
            scopes=self._scopes,
            default_scopes=self._default_scopes,
            workforce_pool_user_project=self._workforce_pool_user_project,
        )
        if not self.is_workforce_pool:
            d.pop("workforce_pool_user_project")
        return d

    @property
    def service_account_email(self):
        """Returns the service account email if service account impersonation is used.

        Returns:
            Optional[str]: The service account email if impersonation is used. Otherwise
                None is returned.
        """
        if self._service_account_impersonation_url:
            # Parse email from URL. The formal looks as follows:
            # https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/name@project-id.iam.gserviceaccount.com:generateAccessToken
            url = self._service_account_impersonation_url
            start_index = url.rfind("/")
            end_index = url.find(":generateAccessToken")
            if start_index != -1 and end_index != -1 and start_index < end_index:
                start_index = start_index + 1
                return url[start_index:end_index]
        return None

    @property
    def is_user(self):
        """Returns whether the credentials represent a user (True) or workload (False).
        Workloads behave similarly to service accounts. Currently workloads will use
        service account impersonation but will eventually not require impersonation.
        As a result, this property is more reliable than the service account email
        property in determining if the credentials represent a user or workload.

        Returns:
            bool: True if the credentials represent a user. False if they represent a
                workload.
        """
        # If service account impersonation is used, the credentials will always represent a
        # service account.
        if self._service_account_impersonation_url:
            return False
        return self.is_workforce_pool

    @property
    def is_workforce_pool(self):
        """Returns whether the credentials represent a workforce pool (True) or
        workload (False) based on the credentials' audience.

        This will also return True for impersonated workforce pool credentials.

        Returns:
            bool: True if the credentials represent a workforce pool. False if they
                represent a workload.
        """
        # Workforce pools representing users have the following audience format:
        # //iam.googleapis.com/locations/$location/workforcePools/$poolId/providers/$providerId
        p = re.compile(r"//iam\.googleapis\.com/locations/[^/]+/workforcePools/")
        return p.match(self._audience or "") is not None

    @property
    def requires_scopes(self):
        """Checks if the credentials requires scopes.

        Returns:
            bool: True if there are no scopes set otherwise False.
        """
        return not self._scopes and not self._default_scopes

    @property
    def project_number(self):
        """Optional[str]: The project number corresponding to the workload identity pool."""

        # STS audience pattern:
        # //iam.googleapis.com/projects/$PROJECT_NUMBER/locations/...
        components = self._audience.split("/")
        try:
            project_index = components.index("projects")
            if project_index + 1 < len(components):
                return components[project_index + 1] or None
        except ValueError:
            return None

    @_helpers.copy_docstring(credentials.Scoped)
    def with_scopes(self, scopes, default_scopes=None):
        return self.__class__(
            **self.constructor_args,
            scopes=scopes,
            default_scopes=default_scopes
        )

    @abc.abstractmethod
    def retrieve_subject_token(self, request):
        """Retrieves the subject token using the credential_source object.

        Args:
            request (google.auth.transport.Request): A callable used to make
                HTTP requests.
        Returns:
            str: The retrieved subject token.
        """
        # pylint: disable=missing-raises-doc
        # (pylint doesn't recognize that this is abstract)
        raise NotImplementedError("retrieve_subject_token must be implemented")

    def get_project_id(self, request):
        """Retrieves the project ID corresponding to the workload identity or workforce pool.
        For workforce pool credentials, it returns the project ID corresponding to
        the workforce_pool_user_project.

        When not determinable, None is returned.

        This is introduced to support the current pattern of using the Auth library:

            credentials, project_id = google.auth.default()

        The resource may not have permission (resourcemanager.projects.get) to
        call this API or the required scopes may not be selected:
        https://cloud.google.com/resource-manager/reference/rest/v1/projects/get#authorization-scopes

        Args:
            request (google.auth.transport.Request): A callable used to make
                HTTP requests.
        Returns:
            Optional[str]: The project ID corresponding to the workload identity pool
                or workforce pool if determinable.
        """
        if self._project_id:
            # If already retrieved, return the cached project ID value.
            return self._project_id
        scopes = self._scopes if self._scopes is not None else self._default_scopes
        # Scopes are required in order to retrieve a valid access token.
        project_number = self.project_number or self._workforce_pool_user_project
        if project_number and scopes:
            headers = {}
            url = _CLOUD_RESOURCE_MANAGER + project_number
            self.before_request(request, "GET", url, headers)
            response = request(url=url, method="GET", headers=headers)

            response_body = (
                response.data.decode("utf-8")
                if hasattr(response.data, "decode")
                else response.data
            )
            response_data = json.loads(response_body)

            if response.status == 200:
                # Cache result as this field is immutable.
                self._project_id = response_data.get("projectId")
                return self._project_id

        return None

    @_helpers.copy_docstring(credentials.Credentials)
    def refresh(self, request):
        scopes = self._scopes if self._scopes is not None else self._default_scopes
        if self._impersonated_credentials:
            self._impersonated_credentials.refresh(request)
            self.token = self._impersonated_credentials.token
            self.expiry = self._impersonated_credentials.expiry
        else:
            now = _helpers.utcnow()
            response_data = self._make_sts_request(request)
            self.token = response_data.get("access_token")
            lifetime = datetime.timedelta(seconds=response_data.get("expires_in"))
            self.expiry = now + lifetime

    def _make_sts_request(self, request):
        """This method is the default method for making STS requests.

        This method is overrideable, as some credential types have alternate
        methods of making this request.
        """
        additional_options = None
        # Do not pass workforce_pool_user_project when client authentication
        # is used. The client ID is sufficient for determining the user project.
        if self._workforce_pool_user_project and not self._client_id:
            additional_options = {"userProject": self._workforce_pool_user_project}
        return self._sts_client.exchange_token(
            request=request,
            grant_type=_STS_GRANT_TYPE,
            subject_token=self.retrieve_subject_token(request),
            subject_token_type=self._subject_token_type,
            audience=self._audience,
            scopes=scopes,
            requested_token_type=_STS_REQUESTED_TOKEN_TYPE,
            additional_options=additional_options,
        )

    @_helpers.copy_docstring(credentials.CredentialsWithQuotaProject)
    def with_quota_project(self, quota_project_id):
        return self.__class__(
            **self.constructor_args,
            quota_project_id=quota_project_id
        )

    @_helpers.copy_docstring(credentials.CredentialsWithTokenUri)
    def with_token_uri(self, token_uri):
        d = dict(
            audience=self._audience,
            subject_token_type=self._subject_token_type,
            token_url=token_uri,
            credential_source=self._credential_source,
            service_account_impersonation_url=self._service_account_impersonation_url,
            service_account_impersonation_options=self._service_account_impersonation_options,
            client_id=self._client_id,
            client_secret=self._client_secret,
            quota_project_id=self._quota_project_id,
            scopes=self._scopes,
            default_scopes=self._default_scopes,
            workforce_pool_user_project=self._workforce_pool_user_project,
        )
        if not self.is_workforce_pool:
            d.pop("workforce_pool_user_project")
        return self.__class__(**d)

    def _initialize_impersonated_credentials(self):
        """Generates an impersonated credentials.

        For more details, see `projects.serviceAccounts.generateAccessToken`_.

        .. _projects.serviceAccounts.generateAccessToken: https://cloud.google.com/iam/docs/reference/credentials/rest/v1/projects.serviceAccounts/generateAccessToken

        Returns:
            impersonated_credentials.Credential: The impersonated credentials
                object.

        Raises:
            google.auth.exceptions.RefreshError: If the generateAccessToken
                endpoint returned an error.
        """
        # Return copy of instance with no service account impersonation.
        source_credentials = self.__class__(**self.constructor_args)

        # Determine target_principal.
        target_principal = self.service_account_email
        if not target_principal:
            raise exceptions.RefreshError(
                "Unable to determine target principal from service account impersonation URL."
            )

        scopes = self._scopes if self._scopes is not None else self._default_scopes
        # Initialize and return impersonated credentials.
        return impersonated_credentials.Credentials(
            source_credentials=source_credentials,
            target_principal=target_principal,
            target_scopes=scopes,
            quota_project_id=self._quota_project_id,
            iam_endpoint_override=self._service_account_impersonation_url,
            lifetime=self._service_account_impersonation_options.get(
                "token_lifetime_seconds"
            ),
        )

    @staticmethod
    def validate_token_url(token_url):
        _TOKEN_URL_PATTERNS = [
            "^[^\\.\\s\\/\\\\]+\\.sts\\.googleapis\\.com$",
            "^sts\\.googleapis\\.com$",
            "^sts\\.[^\\.\\s\\/\\\\]+\\.googleapis\\.com$",
            "^[^\\.\\s\\/\\\\]+\\-sts\\.googleapis\\.com$",
            "^sts\\-[^\\.\\s\\/\\\\]+\\.p\\.googleapis\\.com$",
        ]

        if not Credentials.is_valid_url(_TOKEN_URL_PATTERNS, token_url):
            raise ValueError("The provided token URL is invalid.")

    @staticmethod
    def validate_service_account_impersonation_url(url):
        _SERVICE_ACCOUNT_IMPERSONATION_URL_PATTERNS = [
            "^[^\\.\\s\\/\\\\]+\\.iamcredentials\\.googleapis\\.com$",
            "^iamcredentials\\.googleapis\\.com$",
            "^iamcredentials\\.[^\\.\\s\\/\\\\]+\\.googleapis\\.com$",
            "^[^\\.\\s\\/\\\\]+\\-iamcredentials\\.googleapis\\.com$",
            "^iamcredentials\\-[^\\.\\s\\/\\\\]+\\.p\\.googleapis\\.com$",
        ]

        if not Credentials.is_valid_url(
            _SERVICE_ACCOUNT_IMPERSONATION_URL_PATTERNS, url
        ):
            raise ValueError(
                "The provided service account impersonation URL is invalid."
            )

    @staticmethod
    def is_valid_url(patterns, url):
        """
        Returns True if the provided URL's scheme is HTTPS and the host comforms to at least one of the provided patterns.
        """
        # Check specifically for whitespcaces:
        #   Some python3.6 will parse the space character into %20 and pass the regex check which shouldn't be passed
        if not url or len(str(url).split()) > 1:
            return False

        try:
            uri = parse_url(url)
        except Exception:
            return False

        if not uri.scheme or uri.scheme != "https" or not uri.hostname:
            return False

        return any(re.compile(p).match(uri.hostname.lower()) for p in patterns)

    @classmethod
    def from_info(cls, info, **kwargs):
        """Creates a Credentials instance from parsed external account info.

        Args:
            info (Mapping[str, str]): The external account info in Google
                format.
            kwargs: Additional arguments to pass to the constructor.

        Returns:
            google.auth.identity_pool.Credentials: The constructed
                credentials.

        Raises:
            ValueError: For invalid parameters.
        """
        return cls(
            audience=info.get("audience"),
            subject_token_type=info.get("subject_token_type"),
            token_url=info.get("token_url"),
            service_account_impersonation_url=info.get(
                "service_account_impersonation_url"
            ),
            service_account_impersonation_options=info.get(
                "service_account_impersonation"
            )
            or {},
            client_id=info.get("client_id"),
            client_secret=info.get("client_secret"),
            credential_source=info.get("credential_source"),
            quota_project_id=info.get("quota_project_id"),
            workforce_pool_user_project=info.get("workforce_pool_user_project"),
            **kwargs
        )

    @classmethod
    def from_file(cls, filename, **kwargs):
        """Creates a Credentials instance from an external account json file.

        Args:
            filename (str): The path to the external account json file.
            kwargs: Additional arguments to pass to the constructor.

        Returns:
            google.auth.identity_pool.Credentials: The constructed
                credentials.
        """
        with io.open(filename, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)
            return cls.from_info(data, **kwargs)
