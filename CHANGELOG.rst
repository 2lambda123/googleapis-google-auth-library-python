Changelog
=========

v0.1.0
------

First release with core functionality available. This version is ready for
initial usage and testing.

- Added ``google.auth.credentials``, public interfaces for Credential types. (#8)
- Added ``google.oauth2.credentials``, credentials that use OAuth 2.0 access and refresh tokens (#24)
- Added ``google.oauth2.service_account``, credentials that use Service Account private keys to obtain OAuth 2.0 access tokens. (#25)
- Added ``google.auth.compute_engine``, credentials that use the Compute Engine metadata service to obtain OAuth 2.0 access tokens. (#22)
- Added ``google.auth.jwt.Credentials``, credentials that use a JWT as a bearer token.
- Added ``google.auth.app_engine``, credentials that use the Google App Engine App Identity service to obtain OAuth 2.0 access tokens. (#46)
- Added ``google.auth.default()``, an implementation of Google Application Default Credentials that supports automatic Project ID detection. (#32)
- Added system tests for all credential types. (#51, #54, #56, #58, #59, #60, #61, #62)
- Added ``google.auth.transports.urllib3.AuthorizedHttp``, an HTTP client that includes authentication provided by credentials. (#19)
- Documentation style and formatting updates.

v0.0.1
------

Initial release with foundational functionality for cryptography and JWTs.

- ``google.auth.crypt`` for creating and verifying cryptographic signatures.
- ``google.auth.jwt`` for creating (encoding) and verifying (decoding) JSON Web tokens.
