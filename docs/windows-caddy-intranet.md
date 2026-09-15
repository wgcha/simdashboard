# Windows intranet HTTPS with Caddy

`deploy/windows/Caddyfile.intranet.template` is a manual-certificate template for a Windows intranet host. Copy it outside the repository or render a deployment-local Caddyfile, replace every token, and provide the organization-issued certificate and key. It does not install Caddy, change certificate trust, request a certificate, or create a Windows service.

The route order is intentional: `/api/*` always reaches FastAPI; existing Vite files in `frontend/dist/assets/*` are public frontend build output; missing `/assets/*` routes to FastAPI so legacy backend assets remain subject to the application's authorization policy. All other paths use SPA fallback to `index.html`.

Before an operator runs Caddy, validate the rendered file:

```powershell
.\scripts\windows\validate-caddy-intranet-profile.ps1 -Caddyfile C:\secure-config\Caddyfile -CaddyExecutable C:\tools\caddy.exe
```

Omit `-CaddyExecutable` to perform only the token and route-contract check. Caddy's documented [Caddyfile patterns](https://caddyserver.com/docs/caddyfile/patterns) describe mutually exclusive `handle` routes, and its [`tls` directive](https://caddyserver.com/docs/caddyfile/directives/tls) documents supplying a certificate and key.
