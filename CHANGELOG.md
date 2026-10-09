# Changelog

## 0.4.3

- Add a GPS device tracker using coordinates already present in the encrypted, VIN-checked v6 cloud snapshot; automatic and manual queries update location together with telemetry.
- Validate decimal coordinate strings, ranges and missing values separately so malformed location does not break other telemetry.
- Convert the observed format 1 from GCJ-02 to WGS-84 based on the App 3.2.4 autonavi geocoder call chain; leave unsupported formats unavailable. This interpretation is not a complete manufacturer enum.
- Preserve snapshot timestamps without claiming an independently verified GPS sampling time or accuracy; keep coordinates out of diagnostics.

## 0.4.2

- Add a per-vehicle “立即重新查询车况” button to fetch cloud telemetry immediately, bypassing only the local telemetry cache and resetting the next automatic query time.
- Serialize scheduled and manual account requests; preserve authentication checks, VIN validation and durable failure stops.

## 0.4.1

- Default cloud vehicle polling to 60 minutes; expose an integer-minute interval during SMS setup and encrypted-session import.
- Reject intervals below five minutes in both configuration flows and the runtime client; reuse the configured cache across manual updates and restarts.
- Apply the one-hour default to existing entries without the setting and preserve custom intervals through reauthentication. Allow changing it through reconfiguration.
- Report the effective interval in entity attributes and diagnostics; retain separate local auth checks and metadata caching.

## 0.4.0

- Replace pending vehicle data with VIN-scoped v6 reads, cached for five minutes across manual updates and restarts. Reads may wake the vehicle.
- Add battery percentage, separate CLTC/estimated ranges, four tyre pressures and temperatures, cabin/outside/weather temperatures, and left/right AC setpoints.
- Add doors, covers, window positions, online/connected flags, observed App lock display and the APK's combined operating classification.
- Map 17 charging states, feature-dependent charge target and reservation times. Gate current durations/power by explicit charging modes; retain cached raw durations separately.
- Extend the parser to 129 category model fields, feature presence and homepage metadata without exposing locations or private identity in HA entities.
- Keep missing/null/unknown values unknown; leave odometer and chargedPower unitless, and disable raw seat/steering/state diagnostics by default.
- Show cloud snapshot/retrieval timestamps and source presence; root freshness does not establish individual sensor freshness.
- Reject missing or mismatched response VINs and preserve encrypted responses and a durable failure marker before retry could occur. Authentication remains manual after failure.
- Preserve SMS setup, old credentials, hashed device identities and disabled legacy placeholders. Validate HA lifecycle, cache, crash and cross-VIN isolation using synthetic fixtures.

Mapping evidence covers an LS6. Other models and long-running live HA polling need deployment validation; no vehicle controls, location or active refresh flow are introduced.

## 0.3.0

- Bundle common protocol parameters separately from personal account storage.
- Simplify SMS setup to phone number and verification code, without manual protocol files or key mounts.
- Generate a random local 256-bit account encryption key per HA instance and keep it under the HA configuration directory.
- Persist device identity and encrypted sessions in account directories derived using a keyed phone hash; keep phones and codes out of HA entries.
- Authenticate the local storage marker before reuse; missing or changed keys stop setup instead of silently creating a different account.
- Preserve v0.1/v0.2 encrypted sessions, external keys, custom protocol overrides and existing entity identities.
- Correct the version reported by diagnostics and extend release auditing to permit only the approved common protocol file while still rejecting personal secrets.

Common protocol parameters are public. Personal encryption keys and account sessions are generated locally and never included in releases. Image/risk verification and real vehicle telemetry remain pending.

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
