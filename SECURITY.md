# Security Policy

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/nielsmadan/pratfall/security/advisories/new).
Do not open a public issue. Include the affected version, impact, and reproduction steps. Security
reports are prioritized, but this solo-maintained project has no guaranteed response time.

Fixes target the latest published version. Pre-1.0 versions are not maintained in parallel.

## Trust model

Pratfall launches locally installed agent commands and inherits their environment, working
directory, authentication, configuration, network access, and permission defaults. A configured
`[agents.NAME].command` prefix and `native_args` are trusted executable argv. Only use wrappers and
arguments you trust.

Prompts are passed as argv or stdin data without a shell. In-scope vulnerabilities include shell or
argument injection across that boundary, permission changes that were not explicitly requested,
credential exposure, incorrect process-tree cleanup, and unbounded native output bypassing the
documented limits.
