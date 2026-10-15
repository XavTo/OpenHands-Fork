# OpenHands on Railway

This fork runs OpenHands with a local process runtime inside one Railway service.
Docker-in-Docker and a Docker socket are not required. The instance is intended
for one trusted owner: agent actions can access files and environment variables
inside this container. It is not a multi-tenant sandbox service.

## Access and readiness

Copy `OPENHANDS_BASIC_AUTH_USER` and `OPENHANDS_BASIC_AUTH_PASSWORD` from Railway
variables to sign in. Basic Auth covers both the web app and the outer Socket.IO
transport. Configure Railway's healthcheck as `/health`, with the exact exemption
`OPENHANDS_BASIC_AUTH_EXEMPT_PATHS=/health`; application routes stay protected.

V1 agent callbacks use their own `X-Session-API-Key` validation. The two POST
callback routes delegate to that validation instead of requiring the browser's
Basic Auth credentials. A missing or invalid session key still fails.

## Models

Select a provider and model in OpenHands settings, then enter the provider API
key. A running web UI does not prove that model credentials or a coding task work.
Provider API usage is billed separately by that provider.

## Persistence and local networking

Mount the Railway volume at `/.openhands` and keep `OH_PERSISTENCE_DIR` and
`FILE_STORE_PATH` pointed there. V1 sandbox directories and conversation data use
this persistent storage. For legacy local-runtime sessions, set
`LOCAL_WORKSPACE_BASE=/.openhands/workspace` and
`SANDBOX_LOCAL_RUNTIME_URL=http://127.0.0.1`: the action process runs in the same
container, not at `host.docker.internal`.

The entrypoint and shell command use `exec`, allowing Railway's SIGTERM to reach
the server for its normal shutdown. Give it a 30-second deployment draining
period. Do not rotate the instance secret when replacing containers.

The initial validation reached about 0.66 GB of memory before a real model task.
Allow additional capacity for agent processes and builds; this observation is not
a load-test result. A 512 MB limit can be insufficient.
