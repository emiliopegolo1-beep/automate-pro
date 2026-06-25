#!/usr/bin/env python3
"""Generate a new Gmail API refresh token."""
import os, sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send"
]

CLIENT_CONFIG = {
    "installed": {
        "client_id": "1015260685735-efcs7utghqducgs4enjrb998o0q33dp7.apps.googleusercontent.com",
        "client_secret": "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",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"]
    }
}

print("=" * 60)
print("Opening browser for Gmail authorization...")
print("Sign in with emilio.pegolo1@gmail.com")
print("=" * 60)

flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, SCOPES)
creds = flow.run_local_server(port=0, open_browser=True)

print("\n" + "=" * 60)
print("NEW REFRESH TOKEN:")
print("=" * 60)
print(creds.refresh_token)
print("\nSet this as GMAIL_REFRESH_TOKEN on Railway:")
print(f"  railway variables set GMAIL_REFRESH_TOKEN=\"{creds.refresh_token}\"")
