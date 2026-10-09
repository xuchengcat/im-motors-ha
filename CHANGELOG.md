# Changelog

## 0.2.0

- Add mainland China phone input, SMS send and verification-code login in the HA configuration UI.
- Support SMS reauthentication and login from reconfiguration, retaining existing client identity and entities.
- Create a persistent device identity and encrypted session when only protected protocol configuration and its matching key are supplied.
- Keep encrypted session import available; store only paths and hashed identity in HA entries.
- Allow explicit SMS resend after 60 seconds and resume an existing valid challenge without sending another SMS.
- Retain request markers and encrypted responses before parsing; stop unknown outcomes and provide offline login recovery.
- Pause account polling during authentication and refuse to overwrite a session changed after login began.
- Add translated errors for invalid/expired codes, server rejection, image/risk verification and account binding.

Protocol configuration and its external key remain required. Image/risk verification is not implemented in the HA UI. Vehicle telemetry remains pending. New SMS integration tests use synthetic data and mocked HTTP, with live network blocked.

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
