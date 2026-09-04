// Representative YAML text for mock /api/settings/{name}. Not the real
// config files (the mock layer runs in the browser and cannot read
// config/*.yaml on disk) — just plausible stand-ins with the same shape,
// enough to exercise the /settings editor screen end to end.

export const mockSettingsYaml: Record<string, string> = {
  config: `night_window:
  start: "20:00"
  end: "06:00"
timezone: Asia/Jerusalem
mode: full
api:
  status_push_seconds: 2
`,
  sources: `- name: Janes Defence Weekly
  kind: rss
  url: https://example.test/janes/rss
  enabled: true
- name: Defense News
  kind: rss
  url: https://example.test/defensenews/rss
  enabled: true
- name: GovTribe RFP
  kind: html
  url: https://example.test/govtribe
  enabled: false
`,
  watchlist: `companies:
  - Elbit Systems
  - Rafael Advanced Defense Systems
  - Leonardo DRS
  - HENSOLDT
  - Teledyne FLIR
  - Anduril Industries
keywords:
  - targeting pod
  - EO/IR gimbal
  - counter-UAS
  - ATR
`,
  taxonomy: `# See config/taxonomy.yaml for the authoritative version.
domains:
  airborne_pods:
    label: "פודים ומטע\\"דים אוויריים"
  land_surveillance:
    label: "מטע\\"די תצפית יבשתיים"
  c_uas:
    label: "נגד כטב\\"מים"
`,
  models: `resident: qwen2.5:14b-instruct
light: qwen2.5:7b-instruct
embed: nomic-embed-text
guard_l1: distilbert-guard
guard_l2: qwen2.5:7b-instruct
`,
};
