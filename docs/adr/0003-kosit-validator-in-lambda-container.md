# ADR-0003: Run the KoSIT validator in a Lambda container image
Status: accepted · 2026-10-18

## Context
The KoSIT validator is the reference validator for XRechnung. It is Java, while the rest of the platform is Python.

## Decision
- **AWS:** package the KoSIT validator (pinned version, see `docs/standards.md`) with the XRechnung configuration as a Java **Lambda container image**. The validate step invokes it.
- **Local and CI:** run the same jar in daemon (HTTP) mode with `scripts/kosit.sh` (Java 11+; JDK 21 used). The script downloads checksum-pinned artefacts. Docker is not required.

## Alternatives
- **ECS Fargate service running the daemon.** No cold start, but it is always on and costs money while idle.
- **Python schematron (e.g. Saxon-HE bindings).** A single language, but it is not the reference implementation, so results could differ subtly.
- **Third-party validation API.** Data leaves our boundary, and it adds a vendor dependency.

## Consequences
- ➕ Official validation results with pinned, reproducible versions.
- ➖ JVM cold starts on Lambda. To be measured in W3; SnapStart or provisioned concurrency only if the measurements justify them.
- ➖ The repo now has two runtimes (Python and Java), so an extra container build appears in CI.
