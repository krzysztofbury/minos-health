# Security Policy

## Supported versions

Minos has not published a release yet. Security fixes currently target the
default branch.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Report it privately
through GitHub's **Security** tab using **Report a vulnerability**.

Include, where possible:

- the affected revision or version;
- reproduction steps or a proof of concept;
- the expected and observed impact;
- any suggested mitigation.

You should receive an acknowledgement within seven days. Details will remain
private until a fix or coordinated disclosure is ready.

## Sensitive data

Do not include patient information, prescriptions, credentials, or other
personal data in reports, fixtures, logs, or examples.

The public service is anonymous and must not log raw medical search queries or
encounter payloads.
Security and observability events should use request IDs, route templates,
status classes, bounded timings, and dataset versions instead of request text.

Source datasets, derived databases, container images containing licensed data,
signing material, and deployment credentials must be stored in
the private systems described in
[the operations runbook](docs/v0.0.1/operations.md), not committed to Git.

Local application secret values belong only in ignored `.env` files. Local cloud
credentials stay in external profiles or keychains. Production secret values
are host-managed files readable only by root and the required service UID or
GID, mounted read-only into only that container. Operator and recovery copies
remain in the password manager.
GitHub Actions holds no object-store credentials. The bucket-scoped R2 watcher
key on the Mikr.us host must not be stored in GitHub, `.env`, Compose YAML,
logs, or container images; its recovery copy stays in the password manager.
