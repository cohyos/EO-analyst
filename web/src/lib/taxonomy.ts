// Mirrors config/taxonomy.yaml domain ids/labels for UI filter options. The
// backend remains the source of truth (config, not code, per
// docs/CONVENTIONS.md rule 6) — this is purely a display-label lookup so
// the feed filters and item chips don't need a YAML loader in the browser.

export const DOMAIN_OPTIONS: Array<{ id: string; label: string }> = [
  { id: "airborne_pods", label: "פודים ומטע\"דים אוויריים" },
  { id: "land_surveillance", label: "מטע\"די תצפית יבשתיים" },
  { id: "naval_surveillance", label: "מטע\"די תצפית ימיים" },
  { id: "air_defense", label: "הגנה אווירית ויירוט" },
  { id: "c_uas", label: "נגד כטב\"מים" },
  { id: "computer_vision", label: "בינה חזותית" },
  { id: "secondary", label: "תחומים משיקים" },
];

export function domainLabel(id: string | null | undefined): string {
  return DOMAIN_OPTIONS.find((d) => d.id === id)?.label ?? id ?? "—";
}

// Content review (docs/qa/content_review/CR-ui.md): subdomain ids (config/taxonomy.yaml
// `domains.*.sub`) were rendered as raw slugs wherever they showed up outside the feed's own
// filter chips (e.g. the patents table's "תת-תחום" column: `image_processing`,
// `droic_digital_pixel`, `cv_atr` -- untranslated English keys sitting next to properly-labeled
// columns). Mirrors every domain's `sub` map, same "backend remains the source of truth, this is
// a display-only mirror" caveat as DOMAIN_OPTIONS above. Flat by id -- sub-ids are unique across
// domains in the source config, so no domain-scoping is needed to look one up.
const SUBDOMAIN_LABEL: Record<string, string> = {
  targeting_pods: "פודי ציון מטרות (Targeting Pods)",
  isr_pods: "פודי מודיעין וסיור (ISR/Recce Pods)",
  uav_gimbals: "מטע\"דים ג'ימבליים לכטב\"מים ומסוקים (EO/IR Gimbals)",
  small_gimbals: "מטע\"דים זעירים (Micro-Gimbals)",
  eo_warfare: "לוחמה אלקטרו-אופטית (DIRCM, Laser Dazzlers)",
  strategic_isr: "מטע\"די ISR אסטרטגיים (WAMI, Hyperspectral, SAR+EO fusion)",
  mws: "מערכות התראת טילים (MWS)",
  lorop: "פודי LOROP",
  large_gimbals_16in: "מטע\"דים כדוריים גדולים (16 אינץ')",
  border_towers: "תצפית גבולות ומגדלים (Border & Long-Range Surveillance)",
  mobile_portable: "מערכות תצפית ניידות ונישאות (Mobile/Portable Observation)",
  vehicle_sights: "כוונות מפקד ותותחן ומטע\"די רק\"ם (Commander/Gunner Sights, Vehicle EO/IR Payloads)",
  night_vision: "ראיית לילה אישית (Fused/Digital Night Vision)",
  site_security: "אבטחת מתקנים משולבת EO/מכ\"ם (Perimeter Security)",
  border_lrs: "תצפית ארוכת-טווח להגנת גבולות (Long-Range Border Surveillance)",
  naval_directors: "מערכות EO/IR לספינות (Naval EO Directors, Fire Control)",
  coastal: "תצפית חופית ונמלים (Coastal Surveillance)",
  optronic_masts: "תורני צוללות אופטרוניים (Optronic Masts)",
  usv_payloads: "מטע\"דים לכלי שיט בלתי מאוישים (USV Payloads)",
  iir_seekers: "ראשי ביות אלקטרואופטיים (IIR Seekers)",
  eo_trackers: "עוקבים אלקטרואופטיים במערכות יירוט (EO Trackers)",
  sensor_fusion: "שילוב EO/מכ\"ם (Sensor Fusion)",
  hel: "לייזר בעוצמה גבוהה (High-Energy Laser, HEL)",
  eo_air_surveillance: "גילוי והתראה EO להגנה אווירית (EO Air-Defense Surveillance)",
  hel_weapon_systems: "מערכות נשק לייזר, אוויריות וקרקעיות (Airborne & Ground HEL Weapon Systems)",
  laser_sources_amplifiers:
    "מקורות לייזר, מגברים ומצרפי אלומה (Fiber/Slab/Diode-Pumped Laser Sources, Amplifiers, Beam Combiners, Spectral Beam Combining)",
  beam_control_directors: "בקרת אלומה ומכווני אלומה (Beam Directors, Beam Control, Adaptive Optics)",
  thermal_power_management: "ניהול תרמי והספקה למערכות לייזר (Thermal Management & Power Systems for HEL)",
  detect_track: "גילוי-סיווג-עקיבה EO/IR (Detect/Classify/Track)",
  multi_sensor: "שילוב RF/מכ\"ם/אקוסטיקה (Multi-Sensor C-UAS)",
  effectors: "שיבוש והשמדה (Soft/Hard Kill)",
  convoy_site: "הגנת שיירות ואתרים (Convoy & Site Protection)",
  fpv_swarms: "נגד FPV ונחילים (Counter-FPV / Swarms)",
  atr: "זיהוי מטרות אוטומטי (ATR/ATD)",
  edge_ai: "בינה מלאכותית על גבי מטע\"ד (Edge AI)",
  gps_denied_nav: "ניווט חזותי בסביבת GPS מוגבל (GPS-Denied Navigation)",
  realtime_video: "עיבוד וידאו בזמן אמת",
  multimodal_isr: "מודלים מולטימודליים לניתוח ISR",
  sim2real: "למידה מסימולציה (Sim2Real, Synthetic Data)",
  detectors_fpa: "גלאים ומישורי מוקד (SWIR/MWIR/LWIR, Uncooled, Event Cameras)",
  computational_optics:
    "אופטיקה חישובית וייצוב ג'ימבל (Computational Optics, Gimbal Stabilization, On-Chip Processing)",
  defense_ai_infra: "תשתיות AI ביטחוניות (MLOps, Datasets)",
  droic_digital_pixel: "FPA עם פיקסל דיגיטלי (Digital-Pixel FPA / DROIC / In-Pixel ADC)",
  swir_eswir: "SWIR/eSWIR (InGaAs, Colloidal Quantum Dots)",
  hot_mct_t2sl: "HOT MCT, T2SL, XBn",
  event_based: "גלאים מבוססי אירועים (Neuromorphic / Event Cameras)",
  meta_optics: "אופטיקה מטא-משטחית (Metasurfaces, Flat Optics, Freeform)",
  on_sensor_ai: "בינה מלאכותית על-החיישן (Edge AI, In-Sensor Compute, ATR on FPGA/SoC)",
  laser_lidar: "לייזרים ו-LiDAR (Designators, LiDAR, Laser Dazzlers)",
  cv_atr: "ראייה ממוחשבת לזיהוי מטרות (CV Algorithms for Detection/Tracking/ATR, Multispectral Fusion)",
  image_processing: "עיבוד תמונה (Super-Resolution, Turbulence Mitigation, NUC)",
  microbolometer_uncooled: "מיקרו-בולומטר לא-מקורר (Uncooled Microbolometer)",
};

export function subdomainLabel(id: string | null | undefined): string {
  if (!id) return "—";
  return SUBDOMAIN_LABEL[id] ?? id;
}

// Content review: `ProductLine.subdomains` (`GET /api/product-lines/:id`) comes as composite
// `"<domain>.<subdomain>"` paths (e.g. `airborne_pods.targeting_pods`) -- the product-line detail
// page's header used to render that whole raw path as-is, right below the page title. The
// subdomain half alone is already a specific, self-describing label (translated via
// `subdomainLabel` above), so this drops the domain prefix rather than translating and
// concatenating both halves into a longer chip than the space allows.
export function domainSubdomainLabel(path: string | null | undefined): string {
  if (!path) return "—";
  const sepIndex = path.search(/[./]/);
  if (sepIndex === -1) return subdomainLabel(path);
  return subdomainLabel(path.slice(sepIndex + 1));
}

// Mirrors config/config.yaml `triage.levels` (min score per level; below
// `yellow` = archive). Same "backend remains the source of truth" caveat as
// DOMAIN_OPTIONS above — this is a display-only mirror for the explain-score
// popover, not re-derived logic.
export const LEVEL_THRESHOLDS = { red: 8, orange: 6, yellow: 4 } as const;
