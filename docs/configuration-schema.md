# First Vertical Slice configuration

Configuration is an in-memory/startup input for this slice; persistence,
backup, migration, environment variables, credentials, and UI editing are not
implemented. The exact conceptual object is:

```json
{
  "configVersion": 1,
  "service": {"host": "127.0.0.1", "port": 17391},
  "mock": {"cadenceMilliseconds": 1000},
  "snapshot": {"maxMessages": 100}
}
```

All keys are required and unknown keys are rejected. Values:

- `configVersion`: integer literal `1`.
- `service.host`: constant string `127.0.0.1`; no other address is valid.
- `service.port`: integer 1024–65535; bind conflict is fatal and never selects a
  different port silently.
- `mock.cadenceMilliseconds`: integer 100–60,000.
- `snapshot.maxMessages`: constant integer 100, matching protocol and DOM caps.

No secret field is permitted. CLI spelling and a persistent file location are
deferred to the local-service implementation Task; they must preserve this
schema and fixed default OBS URL `http://127.0.0.1:17391/obs`.

