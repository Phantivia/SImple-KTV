# Security model

This is a **single-user localhost workstation**, not a multi-tenant web service. There is no login, TLS termination, quota isolation between users or internet-facing authorization layer. Keep the Docker port bound to 127.0.0.1; do not forward it to a public interface.

Implemented boundaries: Host validation; strict same-origin writes; required `X-KTV-Client: 1` header; no permissive CORS; typed/allowlisted tasks; random project/asset IDs; project-scoped asset lookup; subprocess argument lists rather than a shell; output path checks; immutable assets; finite numeric validation; file size/duration limits and audio re-decoding; non-root container; same-origin content policy for the app.

These are not a claim of a completed security audit. Parsing untrusted audio still exercises native codecs. Model checkpoint loading can execute pickle code: do not add arbitrary uploads, remote URLs or unknown checkpoints to the registry. Upstream fixed names and recorded SHA256 improve traceability, but hashes are not a trusted signature.

The first model download requires network access to upstream distributors. Audio inference runs locally; the implementation has no analytics or cloud audio-upload endpoint. Application errors/logs may include local paths and model filenames. Redact logs before making a public issue.

Before public deployment, add real authentication/authorization, isolation, TLS, resource quotas, robust streaming request-size enforcement, reverse-proxy hardening and an independent security review. Merely editing KTV_ALLOWED_HOSTS does not make the server safe to expose.

Private recordings, keys, model weights, databases and exports must never be attached to a public bug report. Use a minimal authorized synthetic reproducer. In the initial source bundle there is no provisioned private security-reporting address or actual remote repository; once published, configure GitHub private vulnerability reporting before soliciting sensitive reports.
