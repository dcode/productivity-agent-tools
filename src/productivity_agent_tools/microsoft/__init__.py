"""Microsoft 365 (Outlook REST) stopgap: the OAuth token lifecycle.

While the proper Microsoft Graph client waits on admin consent, this package
authenticates against the gov-cloud Outlook REST API with a refresh token
harvested from a browser sign-in: it is seeded once per Conditional-Access
sign-in window, then renewed silently. The adapter and tools that use it are
added separately; this package is only the credential.
"""
