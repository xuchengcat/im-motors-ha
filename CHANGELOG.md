# Changelog

## 0.1.0

- Initial HACS release of the read-only IM Motors integration.
- Import portable encrypted sessions without storing tokens or keys in HA configuration.
- Register associated vehicle devices and account metadata entities.
- Preserve missing and null metadata as unknown; do not substitute false or zero.
- Refresh account credentials at the server's suggested time and persist rotated credentials.
- Stop requests after failures or interruption until manual review and recovery.
- Provide redacted diagnostics and disabled placeholders for unverified vehicle telemetry.
- Include installation, data mapping, backup and recovery documentation.

Live SOC, range, charging and other vehicle telemetry remain unverified and are not queried.
