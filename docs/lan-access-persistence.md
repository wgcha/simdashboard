# Persistent Windows LAN access

Windows installations resolve the web listener from the running process, root
`.env`, and `backend/.env`, in that order. Existing environment files are
preserved by `deploy.ps1` and `update.ps1`; updates do not overwrite the
listener, authentication, account, or database settings.

When `WINDOWS_WEB_HOST` is absent, an authenticated installation
(`AUTH_MODE=password` or `AUTH_MODE=oidc`) listens on `0.0.0.0`. This keeps an
installed intranet service reachable after an install or update without adding
a machine-specific IP address to source code. `AUTH_MODE=disabled` remains a
loopback-only development default. An explicit setting always wins:

```dotenv
# Keep a password/SSO server behind a local HTTPS reverse proxy.
WINDOWS_WEB_HOST=127.0.0.1

# Or explicitly expose the authenticated Windows listener to the LAN.
WINDOWS_WEB_HOST=0.0.0.0
```

Direct HTTP LAN startup still requires `AUTH_MODE=password` or `AUTH_MODE=oidc`.
It rejects `AUTH_MODE=disabled` and `AUTH_COOKIE_SECURE=true`; use the existing
HTTPS reverse-proxy deployment for secure cookies. These checks do not change
password authentication, accounts, or credentials.

## Employee access URLs

The Windows launcher uses the server computer name and its active corporate
IPv4 addresses. After authentication is configured and the service is started,
employees use either of these forms:

```text
http://SERVER-COMPUTER-NAME/home/
http://CORPORATE-IP/home/
```

The default app path is `/home/`. Existing deployments can preserve another
path with `WINDOWS_WEB_BASE_PATH`; for example,
`WINDOWS_WEB_BASE_PATH=/workbench/` keeps the corresponding `/workbench/`
URL. The application does not alter Windows hosts files, DNS, or firewall
rules. Network access still depends on the existing corporate network policy
and firewall configuration.
