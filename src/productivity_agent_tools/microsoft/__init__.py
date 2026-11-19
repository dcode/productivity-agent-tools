"""Microsoft 365 over the Outlook REST API: sign-in, credential, and the Outlook products.

While the proper Microsoft Graph client waits on admin consent, this package
reads mail and calendar through the Outlook REST API v2.0 with the refresh token
of the webmail single-page app. ``login microsoft`` (``signin.py``) gets one by
driving a browser sign-in, once per Conditional-Access sign-in window; the
credential (``credentials.py``) renews access tokens from it silently. The
``outlook_calendar`` and ``outlook_mail`` products share that one credential.
"""
