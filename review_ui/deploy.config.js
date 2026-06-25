// ──────────────────────────────────────────────────────────────────────────
// deploy.config.js — THE single place to take freight-audit live.
//
// Philosophy: every external dependency is a variable with a working OFFLINE
// STUB as its default. The whole product runs 100% with all of these blank.
// To go live, set the value here (one place) and the matching seam switches
// from its stub to the real service. Empty string "" = "use the offline stub".
//
//   e.g.  API_BASE_URL: ""                 -> engine runs in the browser
//         API_BASE_URL: "https://api…"     -> the app fetches from the server
//
// Nothing else in the code needs editing; each seam reads from CONFIG.
// ──────────────────────────────────────────────────────────────────────────

export const CONFIG = {
  // Phase 0 — branding / address
  DOMAIN_NAME:          "",                                   // LIVE: "app.freightaudit.com"   ("" = preview/localhost URL)

  // Phase 1 — the engine behind an API
  API_BASE_URL:         "",                                   // LIVE: "https://api.freightaudit.com"   ("" = run engine in-browser on samples)

  FINDINGS_URL:         "",                                   // LIVE: "findings.json" (real output of `freight-audit --json`; "" = run ported engine in-browser)

  // Phase 2 — persistence
  DATABASE_URL:         "",                                   // LIVE: "postgres://…"            ("" = localStorage)

  // Phase 3 — auth + comms
  AUTH_MODE:            "mock",                               // LIVE: "clerk" | "auth0" | "custom"      ("mock" = any login enters)
  AUTH_PUBLISHABLE_KEY: "",                                   // LIVE: provider publishable key
  EMAIL_API_KEY:        "",                                   // LIVE: Postmark/SES token        ("" = no email sent)

  // Phase 4 — OCR + document storage
  OCR_PROVIDER:         "fixture",                            // LIVE: "veryfi" | "textract" | "google"  ("fixture" = canned scan)
  OCR_API_KEY:          "",                                   // LIVE: OCR vendor key
  UPLOADS_BUCKET:       "",                                   // LIVE: "s3://freightaudit-docs"  ("" = local blob)

  // Phase 5 — pilot integrations
  QBO_CLIENT_ID:        "",                                   // LIVE: QuickBooks OAuth app id   ("" = .iif file download)
  TMS_API_URL:          "",                                   // LIVE: TMS endpoint              ("" = tms.json download)
  TMS_API_TOKEN:        "",                                   // LIVE: TMS token
  STRIPE_KEY:           "",                                   // LIVE: "sk_live_…"               ("" = mock checkout, no charge)
  CLIENT_PROFILE_PATH:  "samples/profiles/client_example.json", // LIVE: the client's own ClientProfile JSON

  // Phase 6 — scale / enterprise
  QUEUE_URL:            "",                                   // LIVE: "redis://…" | SQS         ("" = synchronous, in-process)
  SSO_METADATA_URL:     "",                                   // LIVE: Okta/Azure metadata URL   ("" = local auth)
  MONITORING_DSN:       "",                                   // LIVE: Sentry DSN                ("" = console)
  CDN_URL:              ""                                    // LIVE: CDN base URL              ("" = served by the app)
};

// A value counts as "live" once it is a real, non-stub value.
export function isLive(key) {
  const v = CONFIG[key];
  return !!v && v !== "mock" && v !== "fixture";
}

// Metadata for the in-app Deployment Config view — kept beside the values so
// there is one source of truth for both behaviour and documentation.
export const EXTERNALS = [
  { key: "DOMAIN_NAME",        phase: "0", label: "Domain name",     stub: "Preview / localhost URL",            live: "app.freightaudit.com",      seam: "Base URL for links and outbound email" },
  { key: "API_BASE_URL",       phase: "1", label: "Engine API",      stub: "Engine runs in-browser on samples",  live: "https://api.freightaudit.com", seam: "loadEngine(): fetch /findings vs local runBatch()" },
  { key: "DATABASE_URL",       phase: "2", label: "Database",        stub: "Decisions + audit log in localStorage", live: "postgres://…",            seam: "store adapter (read/write decisions)" },
  { key: "AUTH_MODE",          phase: "3", label: "Authentication",  stub: "Mock login, any credentials enter",  live: "clerk | auth0 | custom",    seam: "signIn() / register()" },
  { key: "EMAIL_API_KEY",      phase: "3", label: "Email",           stub: "No-op, no email sent",               live: "Postmark / SES token",      seam: "password reset, invites" },
  { key: "OCR_PROVIDER",       phase: "4", label: "OCR",             stub: "Fixture scan + canned fields",       live: "veryfi | textract | google", seam: "extract provider seam" },
  { key: "UPLOADS_BUCKET",     phase: "4", label: "Document storage",stub: "Local in-memory blob",               live: "s3://freightaudit-docs",    seam: "upload handler" },
  { key: "QBO_CLIENT_ID",      phase: "5", label: "QuickBooks",      stub: "Downloads a .iif file",              live: "QuickBooks OAuth app id",   seam: "exporter: IIF" },
  { key: "TMS_API_URL",        phase: "5", label: "TMS write-back",  stub: "Downloads tms.json",                 live: "TMS endpoint + token",      seam: "exporter: TMS JSON" },
  { key: "STRIPE_KEY",         phase: "5", label: "Payments",        stub: "Mock checkout, no charge",           live: "sk_live_…",                 seam: "register() / pay" },
  { key: "CLIENT_PROFILE_PATH",phase: "5", label: "Client profile",  stub: "Acme sample profile",                live: "Client's ClientProfile JSON", seam: "rules \u2192 engine" },
  { key: "QUEUE_URL",          phase: "6", label: "Job queue",       stub: "Synchronous, in-process",            live: "redis://… | SQS",           seam: "batch processing" },
  { key: "SSO_METADATA_URL",   phase: "6", label: "Enterprise SSO",  stub: "Local auth",                         live: "Okta / Azure metadata",     seam: "auth" },
  { key: "MONITORING_DSN",     phase: "6", label: "Monitoring",      stub: "Console logs",                       live: "Sentry DSN",                seam: "error reporting" }
];
