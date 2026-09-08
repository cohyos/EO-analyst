// PD-vocab-ui (2026-09-09): client-side mirror of `config/spec_vocabulary.yaml`, the fixed
// specification vocabulary (docs/PLAN_SPEC_VOCABULARY.md) that fixes the free-named-parameter
// drift shown in that doc's opening example (the same fact -- "20-inch-class aperture performance
// inside a 15-inch envelope" -- written four different ways across three real dossier runs). This
// file exists so `DossierSpecTable`/`DossierComparisonView` can group and order rows by a fixed
// vocabulary even before/independent of a backend endpoint serving it (per the plan's own
// lane-(c) doc note: "check whether the backend exposes GET /api/dossiers/vocabulary/{product_line}
// per the plan; if not, read config/spec_vocabulary.yaml at build time into a generated TS module
// as an interim" -- as of this file's authoring, `agent/eoa/dossier/vocabulary.py` and any such
// endpoint do not exist yet (PD-vocab-extract lane not landed), so this is that interim).
//
// Hand-synced (not build-time codegen), same convention `web/src/lib/productLines.ts` already
// uses for `config/product_lines.yaml`. Only the fields the client actually needs are ported
// (`key`/`label_he`/`label_en`/`group_he`/`unit`/`required` -- NOT `synonyms`/`value_type`/
// `enum_values`/`notes_he`, which are extraction-time-only per that doc's §5 file list).
//
// Regenerating after `config/spec_vocabulary.yaml` changes: the block below was produced by
// reading that YAML with a one-off `yaml.safe_load` + template script (not checked in -- the
// output is this file, hand-reviewed and committed). `specVocabulary.test.ts` pins the exact
// key/count contract (121 total: 24 common + 19/17/16/15/15/15 per line) as a regression guard so
// a future hand-edit here that drifts from the YAML fails a test rather than silently rotting.
//
// group_he -- the fixed 8 groups, in display order (docs/PLAN_SPEC_VOCABULARY.md §2): אופטיקה,
// חיישנים, לייזר, ייצוב ובקרה, מכניקה וסביבה, ממשקים, ביצועי מערכת, בשלות ולוגיסטיקה.

export interface SpecVocabParam {
  key: string;
  labelHe: string;
  labelEn: string;
  /** Short Hebrew/English unit string, or null when not a measured quantity. */
  unit: string | null;
  groupHe: string;
  /** true: the row always renders in `DossierSpecTable`/comparison view, "לא נמצא במקורות" when
   * no value was found -- distinct from a genuinely-not-applicable, non-required row. */
  required: boolean;
}

/** Fixed display order for the 8 `group_he` values -- every grouped table (single dossier,
 * comparison, product-line report) renders sub-tables/row-groups in this order. */
export const SPEC_GROUP_ORDER: string[] = [
  "אופטיקה",
  "חיישנים",
  "לייזר",
  "ייצוב ובקרה",
  "מכניקה וסביבה",
  "ממשקים",
  "ביצועי מערכת",
  "בשלות ולוגיסטיקה",
];

const COMMON_PARAMS: SpecVocabParam[] = [
  {
    key: "field_of_view",
    labelHe: "שדה ראייה",
    labelEn: "Field of View (FOV)",
    unit: "מעלות (°) / מיליראד (mrad)",
    groupHe: "אופטיקה",
    required: true,
  },
  {
    key: "optical_aperture",
    labelHe: "קוטר אפרטורה אופטית",
    labelEn: "Optical Aperture Diameter",
    unit: "אינץ׳ / מ״מ",
    groupHe: "אופטיקה",
    required: false,
  },
  {
    key: "zoom_range",
    labelHe: "טווח זום",
    labelEn: "Zoom Range",
    unit: "פי (x) / מ״מ (טווח מוקד)",
    groupHe: "אופטיקה",
    required: false,
  },
  {
    key: "detector_type",
    labelHe: "סוג/י גלאי",
    labelEn: "Detector Type(s)",
    unit: null,
    groupHe: "חיישנים",
    required: true,
  },
  {
    key: "resolution",
    labelHe: "רזולוציה (מערך גלאי)",
    labelEn: "Detector Resolution",
    unit: "פיקסלים",
    groupHe: "חיישנים",
    required: false,
  },
  {
    key: "netd",
    labelHe: "רגישות תרמית (NETD)",
    labelEn: "Noise Equivalent Temperature Difference (NETD)",
    unit: "mK",
    groupHe: "חיישנים",
    required: false,
  },
  {
    key: "spectral_channels_count",
    labelHe: "מספר ערוצים ספקטרליים",
    labelEn: "Spectral Channel Count",
    unit: "ערוצים",
    groupHe: "חיישנים",
    required: false,
  },
  {
    key: "detection_range_dri",
    labelHe: "טווח זיהוי/הכרה/זיהוי-ודאי (DRI)",
    labelEn: "Detection/Recognition/Identification (DRI) Range",
    unit: "ק״מ",
    groupHe: "ביצועי מערכת",
    required: true,
  },
  {
    key: "size_to_performance_ratio",
    labelHe: "יחס ביצועים-למעטפת",
    labelEn: "Aperture-to-Envelope / Size-to-Performance Ratio",
    unit: null,
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "adverse_weather_performance",
    labelHe: "ביצועים בתנאי מזג אוויר קשים",
    labelEn: "Adverse-Weather / Obscurant Penetration Performance",
    unit: null,
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "target_tracker_atr",
    labelHe: "עוקב מטרות / זיהוי אוטומטי (ATR)",
    labelEn: "Target Tracker / Automatic Target Recognition (ATR)",
    unit: null,
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "line_of_sight_stabilization",
    labelHe: "ייצוב קו ראייה",
    labelEn: "Line-of-Sight (LOS) Stabilization",
    unit: "מיקרו-רדיאן (µrad, RMS)",
    groupHe: "ייצוב ובקרה",
    required: true,
  },
  {
    key: "gimbal_axes_class",
    labelHe: "גימבל -- מספר צירים וסוג",
    labelEn: "Gimbal Axes Count / Class",
    unit: "צירים",
    groupHe: "ייצוב ובקרה",
    required: false,
  },
  {
    key: "inertial_sensors_imu",
    labelHe: "חיישני אינרציה (IMU)",
    labelEn: "Inertial Measurement Unit (IMU) Sensors",
    unit: null,
    groupHe: "ייצוב ובקרה",
    required: false,
  },
  {
    key: "laser_rangefinder",
    labelHe: "מד-טווח לייזר (LRF)",
    labelEn: "Laser Range Finder (LRF)",
    unit: "ק״מ",
    groupHe: "לייזר",
    required: true,
  },
  {
    key: "laser_designator_illuminator",
    labelHe: "מצביע/מאיר לייזר",
    labelEn: "Laser Designator / Illuminator",
    unit: null,
    groupHe: "לייזר",
    required: false,
  },
  {
    key: "weight",
    labelHe: "משקל",
    labelEn: "Weight",
    unit: "ק״ג",
    groupHe: "מכניקה וסביבה",
    required: true,
  },
  {
    key: "envelope_dimensions",
    labelHe: "נפח/מעטפת",
    labelEn: "Envelope Dimensions / Volume",
    unit: "מ״מ / ליטר",
    groupHe: "מכניקה וסביבה",
    required: true,
  },
  {
    key: "environmental_qualification",
    labelHe: "תנאי סביבה (הסמכה)",
    labelEn: "Environmental Qualification",
    unit: null,
    groupHe: "מכניקה וסביבה",
    required: true,
  },
  {
    key: "power_consumption",
    labelHe: "הספק / צריכת חשמל",
    labelEn: "Power Consumption",
    unit: "ואט (W) / וולט (V)",
    groupHe: "ממשקים",
    required: true,
  },
  {
    key: "interfaces",
    labelHe: "ממשקי תקשורת/נתונים",
    labelEn: "Communication/Data Interfaces",
    unit: null,
    groupHe: "ממשקים",
    required: true,
  },
  {
    key: "gnss_receiver",
    labelHe: "מקלט ניווט (GNSS)",
    labelEn: "GNSS/Navigation Receiver",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "trl_status",
    labelHe: "TRL / סטטוס בשלות",
    labelEn: "Technology Readiness Level (TRL) / Status",
    unit: null,
    groupHe: "בשלות ולוגיסטיקה",
    required: true,
  },
  {
    key: "platforms",
    labelHe: "פלטפורמות נשא",
    labelEn: "Carrier Platforms",
    unit: null,
    groupHe: "בשלות ולוגיסטיקה",
    required: false,
  },
];

const LINE_PARAMS_TARGETING_PODS: SpecVocabParam[] = [
  {
    key: "laser_designation_accuracy",
    labelHe: "דיוק ציון מטרות בלייזר",
    labelEn: "Laser Designation Accuracy",
    unit: "מטר / מיליראד",
    groupHe: "לייזר",
    required: true,
  },
  {
    key: "laser_code_compliance",
    labelHe: "קודי לייזר נתמכים",
    labelEn: "Laser Code Compliance",
    unit: null,
    groupHe: "לייזר",
    required: false,
  },
  {
    key: "boresight_retention",
    labelHe: "שימור כיוונון (Boresight Retention)",
    labelEn: "Boresight Retention",
    unit: null,
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "weapon_interface_compatibility",
    labelHe: "תאימות לתחמושת מונחית-לייזר",
    labelEn: "Laser-Guided Weapon Interface Compatibility",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "pylon_station_interface",
    labelHe: "ממשק תלייה / עמדת נשיאה",
    labelEn: "Pylon/Station Interface",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "cooling_system",
    labelHe: "מערכת קירור",
    labelEn: "Cooling System",
    unit: null,
    groupHe: "מכניקה וסביבה",
    required: false,
  },
  {
    key: "common_aperture_telescope",
    labelHe: "טלסקופ ספוטר משותף",
    labelEn: "Common-Aperture Spotter Telescope",
    unit: "אינץ׳",
    groupHe: "אופטיקה",
    required: false,
  },
  {
    key: "pod_class_diameter",
    labelHe: "מחלקת קוטר המטע״ד",
    labelEn: "Pod-Class Diameter",
    unit: "אינץ׳",
    groupHe: "מכניקה וסביבה",
    required: false,
  },
  {
    key: "laser_spot_tracker",
    labelHe: "גלאי מעקב נקודת לייזר",
    labelEn: "Laser Spot Tracker (Quadrant Detector)",
    unit: null,
    groupHe: "לייזר",
    required: false,
  },
  {
    key: "eo_ir_dual_band_simultaneous",
    labelHe: "ראייה סימולטנית רב-ספקטרלית",
    labelEn: "Simultaneous Multi-Band EO/IR Imaging",
    unit: null,
    groupHe: "חיישנים",
    required: false,
  },
  {
    key: "video_tracker_channels",
    labelHe: "ערוצי מעקב וידאו",
    labelEn: "Video Tracker Channels",
    unit: "ערוצים",
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "designation_range_effective",
    labelHe: "טווח ציון מטרות אפקטיבי",
    labelEn: "Effective Designation Range",
    unit: "ק״מ",
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "ai_target_recognition",
    labelHe: "זיהוי/סיווג מטרות מבוסס AI",
    labelEn: "AI-Based Target Recognition/Classification",
    unit: null,
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "nvg_compatible_marking",
    labelHe: "סימון תואם משקפי ראיית לילה (NVG)",
    labelEn: "NVG-Compatible Marking",
    unit: null,
    groupHe: "לייזר",
    required: false,
  },
  {
    key: "field_of_view_multiplicity",
    labelHe: "ריבוי שדות ראייה (WFOV/NFOV/Spot)",
    labelEn: "Field-of-View Multiplicity",
    unit: "מספר שדות",
    groupHe: "אופטיקה",
    required: false,
  },
  {
    key: "slant_range_performance",
    labelHe: "ביצועים בטווח אלכסוני (Slant Range)",
    labelEn: "Slant Range Performance",
    unit: "ק״מ / רגל",
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "store_management_integration",
    labelHe: "אינטגרציה עם ניהול תחמושת",
    labelEn: "Stores Management System (SMS) Integration",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "aircraft_platform_compatibility",
    labelHe: "תאימות לפלטפורמות מטוסי קרב",
    labelEn: "Fighter Aircraft Platform Compatibility",
    unit: null,
    groupHe: "בשלות ולוגיסטיקה",
    required: false,
  },
  {
    key: "mtbf_pod",
    labelHe: "אמינות (MTBF)",
    labelEn: "Mean Time Between Failures (MTBF)",
    unit: "שעות",
    groupHe: "בשלות ולוגיסטיקה",
    required: false,
  },
];

const LINE_PARAMS_MWS_EO: SpecVocabParam[] = [
  {
    key: "threat_coverage_types",
    labelHe: "סוגי איומים מכוסים",
    labelEn: "Threat Coverage Types",
    unit: null,
    groupHe: "ביצועי מערכת",
    required: true,
  },
  {
    key: "spectral_band_coverage",
    labelHe: "כיסוי ספקטרלי (UV/MWIR)",
    labelEn: "Spectral Band Coverage",
    unit: null,
    groupHe: "חיישנים",
    required: true,
  },
  {
    key: "false_alarm_rate",
    labelHe: "שיעור התרעות שווא",
    labelEn: "False Alarm Rate (FAR)",
    unit: "התרעות/שעת טיסה",
    groupHe: "ביצועי מערכת",
    required: true,
  },
  {
    key: "declaration_time",
    labelHe: "זמן הכרזת איום",
    labelEn: "Threat Declaration Time",
    unit: "מילישניות (ms) / שניות",
    groupHe: "ביצועי מערכת",
    required: true,
  },
  {
    key: "coverage_sectors",
    labelHe: "מספר סקטורי/ראשי כיסוי",
    labelEn: "Coverage Sectors / Sensor Head Count",
    unit: "יחידות",
    groupHe: "ביצועי מערכת",
    required: true,
  },
  {
    key: "dircm_integration",
    labelHe: "אינטגרציה עם DIRCM",
    labelEn: "DIRCM Integration",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "countermeasure_cueing",
    labelHe: "הכוונת אמצעי הגנה",
    labelEn: "Countermeasure Cueing",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "detection_range_missile",
    labelHe: "טווח גילוי טיל",
    labelEn: "Missile Detection Range",
    unit: "ק״מ",
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "probability_of_detection",
    labelHe: "הסתברות גילוי (Pd)",
    labelEn: "Probability of Detection (Pd)",
    unit: "אחוזים (%)",
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "sensor_count_per_platform",
    labelHe: "מספר חיישנים לפלטפורמה",
    labelEn: "Sensor Count per Platform",
    unit: "יחידות",
    groupHe: "מכניקה וסביבה",
    required: false,
  },
  {
    key: "weight_per_sensor_head",
    labelHe: "משקל ליחידת חיישן",
    labelEn: "Weight per Sensor Head",
    unit: "ק״ג",
    groupHe: "מכניקה וסביבה",
    required: false,
  },
  {
    key: "mtbf_mws",
    labelHe: "אמינות (MTBF)",
    labelEn: "Mean Time Between Failures (MTBF)",
    unit: "שעות",
    groupHe: "בשלות ולוגיסטיקה",
    required: false,
  },
  {
    key: "uv_solar_blind_capability",
    labelHe: "יכולת UV עיוור-שמש",
    labelEn: "UV Solar-Blind Capability",
    unit: null,
    groupHe: "חיישנים",
    required: false,
  },
  {
    key: "passive_operation",
    labelHe: "פעולה פסיבית (ללא פליטה)",
    labelEn: "Passive-Only Operation",
    unit: null,
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "software_reprogrammability",
    labelHe: "עדכון תוכנה / ספריית איומים",
    labelEn: "Software/Threat-Library Reprogrammability",
    unit: null,
    groupHe: "בשלות ולוגיסטיקה",
    required: false,
  },
  {
    key: "irst_secondary_capability",
    labelHe: "יכולת IRST משנית",
    labelEn: "Secondary IRST Capability",
    unit: null,
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "integration_test_status",
    labelHe: "סטטוס שילוב/הסמכה בפלטפורמה",
    labelEn: "Platform Integration/Certification Status",
    unit: null,
    groupHe: "בשלות ולוגיסטיקה",
    required: false,
  },
];

const LINE_PARAMS_LOROP_PODS: SpecVocabParam[] = [
  {
    key: "standoff_range",
    labelHe: "טווח עמידה (Standoff Range)",
    labelEn: "Standoff Range",
    unit: "ק״מ / נ״מ (nm)",
    groupHe: "ביצועי מערכת",
    required: true,
  },
  {
    key: "gsd",
    labelHe: "רזולוציית קרקע (GSD)",
    labelEn: "Ground Sample Distance (GSD)",
    unit: "ס״מ / מ׳ לפיקסל",
    groupHe: "אופטיקה",
    required: true,
  },
  {
    key: "niirs_rating",
    labelHe: "דירוג NIIRS",
    labelEn: "National Imagery Interpretability Rating Scale (NIIRS)",
    unit: "דירוג (0-9)",
    groupHe: "אופטיקה",
    required: false,
  },
  {
    key: "swath_width",
    labelHe: "רוחב רצועת סריקה",
    labelEn: "Swath Width",
    unit: "ק״מ",
    groupHe: "אופטיקה",
    required: false,
  },
  {
    key: "sensor_type_film_digital",
    labelHe: "סוג חיישן (סרט/דיגיטלי)",
    labelEn: "Sensor Type (Film/Digital)",
    unit: null,
    groupHe: "חיישנים",
    required: true,
  },
  {
    key: "data_link_bandwidth",
    labelHe: "קצב שידור קישור נתונים",
    labelEn: "Data Link Bandwidth",
    unit: "Mbps/Gbps",
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "image_storage_capacity",
    labelHe: "קיבולת אחסון תמונות",
    labelEn: "Onboard Image Storage Capacity",
    unit: "TB/GB",
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "altitude_envelope",
    labelHe: "מעטפת גובה תפעולי",
    labelEn: "Operating Altitude Envelope",
    unit: "רגל / מטר",
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "speed_envelope",
    labelHe: "מעטפת מהירות תפעולית",
    labelEn: "Operating Speed Envelope",
    unit: "קשר (kt) / מאך",
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "geolocation_accuracy",
    labelHe: "דיוק מיקום גיאוגרפי",
    labelEn: "Geolocation Accuracy",
    unit: "מטר (CEP)",
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "spectral_bands_count",
    labelHe: "מספר פסי ספקטרום",
    labelEn: "Number of Spectral Bands",
    unit: "פסים",
    groupHe: "חיישנים",
    required: false,
  },
  {
    key: "real_time_downlink",
    labelHe: "שידור בזמן אמת",
    labelEn: "Real-Time Downlink",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "focal_length",
    labelHe: "אורך מוקד עדשה",
    labelEn: "Focal Length",
    unit: "מ״מ / אינץ׳",
    groupHe: "אופטיקה",
    required: false,
  },
  {
    key: "image_motion_compensation",
    labelHe: "פיצוי תזוזת תמונה (IMC/FMC)",
    labelEn: "Image/Forward Motion Compensation (IMC/FMC)",
    unit: null,
    groupHe: "אופטיקה",
    required: false,
  },
  {
    key: "mission_duration",
    labelHe: "משך משימה",
    labelEn: "Mission Duration",
    unit: "שעות",
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "onboard_processing_exploitation",
    labelHe: "עיבוד/ניצול תמונה על-סיפון",
    labelEn: "Onboard Processing/Exploitation",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
];

const LINE_PARAMS_EO_AIR_DEFENSE_WARNING: SpecVocabParam[] = [
  {
    key: "search_volume",
    labelHe: "נפח חיפוש",
    labelEn: "Search Volume",
    unit: "מעלות אזימוט × מעלות רום",
    groupHe: "ביצועי מערכת",
    required: true,
  },
  {
    key: "revisit_rate",
    labelHe: "קצב סריקה חוזרת",
    labelEn: "Revisit Rate",
    unit: "שניות / הרץ (Hz)",
    groupHe: "ביצועי מערכת",
    required: true,
  },
  {
    key: "track_capacity",
    labelHe: "קיבולת מעקב (מספר מטרות)",
    labelEn: "Track Capacity",
    unit: "מטרות בו-זמנית",
    groupHe: "ביצועי מערכת",
    required: true,
  },
  {
    key: "cueing_interfaces",
    labelHe: "ממשקי הכוונה",
    labelEn: "Cueing Interfaces",
    unit: null,
    groupHe: "ממשקים",
    required: true,
  },
  {
    key: "c_uas_integration",
    labelHe: "אינטגרציה עם מערכות C-UAS",
    labelEn: "C-UAS System Integration",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "air_defense_integration",
    labelHe: "אינטגרציה עם הגנה אווירית/פיקוד",
    labelEn: "Air Defense/C2 System Integration",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "detection_range_small_uas",
    labelHe: "טווח גילוי רחפנים קטנים",
    labelEn: "Small UAS Detection Range",
    unit: "ק״מ / מ׳",
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "classification_capability",
    labelHe: "יכולת סיווג מטרות",
    labelEn: "Target Classification Capability",
    unit: null,
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "false_alarm_rate_ad",
    labelHe: "שיעור התרעות שווא",
    labelEn: "False Alarm Rate",
    unit: "התרעות/יום",
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "scan_pattern",
    labelHe: "תבנית סריקה",
    labelEn: "Scan Pattern",
    unit: null,
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "number_of_sensor_heads",
    labelHe: "מספר ראשי חיישן במערך",
    labelEn: "Number of Sensor Heads",
    unit: "יחידות",
    groupHe: "מכניקה וסביבה",
    required: false,
  },
  {
    key: "fusion_with_radar",
    labelHe: "מיזוג נתונים עם מכ״ם",
    labelEn: "Radar Data Fusion",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "passive_detection",
    labelHe: "גילוי פסיבי (ללא פליטה)",
    labelEn: "Passive Detection",
    unit: null,
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "day_night_all_weather",
    labelHe: "פעולה יום/לילה/כל-מזג-אוויר",
    labelEn: "Day/Night/All-Weather Operation",
    unit: null,
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "slew_to_cue_time",
    labelHe: "זמן תגובה מהכוונה לנעילה",
    labelEn: "Slew-to-Cue Time",
    unit: "שניות",
    groupHe: "ייצוב ובקרה",
    required: false,
  },
];

const LINE_PARAMS_BALL_GIMBALS_16IN: SpecVocabParam[] = [
  {
    key: "ball_diameter",
    labelHe: "קוטר הכדור",
    labelEn: "Ball Diameter",
    unit: "אינץ׳",
    groupHe: "מכניקה וסביבה",
    required: true,
  },
  {
    key: "payload_channel_count",
    labelHe: "מספר ערוצי תשלובת",
    labelEn: "Payload Channel Count",
    unit: "ערוצים",
    groupHe: "חיישנים",
    required: true,
  },
  {
    key: "slew_rate",
    labelHe: "קצב סיבוב/סריקה",
    labelEn: "Slew Rate",
    unit: "מעלות/שנייה (°/s)",
    groupHe: "ייצוב ובקרה",
    required: true,
  },
  {
    key: "stabilization_class_microrad",
    labelHe: "סיווג ייצוב (מיקרו-רדיאן)",
    labelEn: "Stabilization Class (µrad RMS)",
    unit: "מיקרו-רדיאן (µrad, RMS)",
    groupHe: "ייצוב ובקרה",
    required: true,
  },
  {
    key: "mounting_type",
    labelHe: "סוג התקנה",
    labelEn: "Mounting Type",
    unit: null,
    groupHe: "מכניקה וסביבה",
    required: true,
  },
  {
    key: "field_of_regard",
    labelHe: "שדה כיסוי (Field of Regard)",
    labelEn: "Field of Regard (FOR)",
    unit: "מעלות",
    groupHe: "ייצוב ובקרה",
    required: false,
  },
  {
    key: "payload_weight_capacity",
    labelHe: "קיבולת משקל תשלובת",
    labelEn: "Payload Weight Capacity",
    unit: "ק״ג",
    groupHe: "מכניקה וסביבה",
    required: false,
  },
  {
    key: "power_supply_voltage",
    labelHe: "מתח הספקה",
    labelEn: "Power Supply Voltage",
    unit: "וולט (V)",
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "environmental_sealing",
    labelHe: "איטום סביבתי (דירוג IP)",
    labelEn: "Environmental Sealing (IP Rating)",
    unit: null,
    groupHe: "מכניקה וסביבה",
    required: false,
  },
  {
    key: "laser_designator_option",
    labelHe: "אופציית מצביע לייזר",
    labelEn: "Laser Designator Option",
    unit: null,
    groupHe: "לייזר",
    required: false,
  },
  {
    key: "multi_sensor_fusion",
    labelHe: "מיזוג חיישנים (EO/IR/LRF)",
    labelEn: "Multi-Sensor Fusion",
    unit: null,
    groupHe: "חיישנים",
    required: false,
  },
  {
    key: "platform_types_supported",
    labelHe: "סוגי פלטפורמות נתמכות",
    labelEn: "Supported Platform Types",
    unit: null,
    groupHe: "בשלות ולוגיסטיקה",
    required: false,
  },
  {
    key: "continuous_zoom",
    labelHe: "זום רציף",
    labelEn: "Continuous Zoom",
    unit: null,
    groupHe: "אופטיקה",
    required: false,
  },
  {
    key: "az_el_travel_limits",
    labelHe: "טווחי תנועה אזימוט/רום",
    labelEn: "Azimuth/Elevation Travel Limits",
    unit: "מעלות",
    groupHe: "ייצוב ובקרה",
    required: false,
  },
  {
    key: "vibration_shock_rating",
    labelHe: "עמידות ברעידות/זעזועים",
    labelEn: "Vibration/Shock Rating",
    unit: null,
    groupHe: "מכניקה וסביבה",
    required: false,
  },
];

const LINE_PARAMS_BORDER_LONG_RANGE_EO: SpecVocabParam[] = [
  {
    key: "mast_tower_mounting",
    labelHe: "התקנה על תורן/מגדל",
    labelEn: "Mast/Tower Mounting",
    unit: "מטר (גובה התקנה)",
    groupHe: "מכניקה וסביבה",
    required: true,
  },
  {
    key: "dri_at_long_range",
    labelHe: "DRI בטווח ארוך",
    labelEn: "Long-Range DRI",
    unit: "ק״מ",
    groupHe: "ביצועי מערכת",
    required: true,
  },
  {
    key: "duty_cycle_24_7",
    labelHe: "מחזור פעולה 24/7",
    labelEn: "24/7 Duty Cycle",
    unit: null,
    groupHe: "בשלות ולוגיסטיקה",
    required: true,
  },
  {
    key: "c2_integration",
    labelHe: "אינטגרציה עם פיקוד ובקרה (C2)",
    labelEn: "C2/VMS Integration",
    unit: null,
    groupHe: "ממשקים",
    required: true,
  },
  {
    key: "thermal_camera_class",
    labelHe: "סיווג מצלמה תרמית",
    labelEn: "Thermal Camera Class",
    unit: null,
    groupHe: "חיישנים",
    required: true,
  },
  {
    key: "pan_tilt_range",
    labelHe: "טווח פאן/טילט",
    labelEn: "Pan/Tilt Range",
    unit: "מעלות",
    groupHe: "ייצוב ובקרה",
    required: false,
  },
  {
    key: "weatherproofing_rating",
    labelHe: "דירוג עמידות למזג אוויר",
    labelEn: "Weatherproofing Rating",
    unit: null,
    groupHe: "מכניקה וסביבה",
    required: false,
  },
  {
    key: "perimeter_coverage_length",
    labelHe: "אורך היקף מכוסה ליחידה",
    labelEn: "Perimeter Coverage Length per Unit",
    unit: "ק״מ",
    groupHe: "ביצועי מערכת",
    required: false,
  },
  {
    key: "radar_fusion",
    labelHe: "מיזוג עם מכ״ם קרקעי",
    labelEn: "Ground Radar Fusion",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "number_of_units_per_site",
    labelHe: "מספר יחידות/עמדות לאתר",
    labelEn: "Number of Units per Site",
    unit: "יחידות",
    groupHe: "בשלות ולוגיסטיקה",
    required: false,
  },
  {
    key: "remote_monitoring_capability",
    labelHe: "יכולת ניטור/הפעלה מרחוק",
    labelEn: "Remote Monitoring/Operation Capability",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "vms_software_integration",
    labelHe: "אינטגרציה עם תוכנת ניהול תצפית",
    labelEn: "Video Management Software (VMS) Integration",
    unit: null,
    groupHe: "ממשקים",
    required: false,
  },
  {
    key: "lighting_illuminator_support",
    labelHe: "תמיכה בתאורת IR/לייזר מאיר",
    labelEn: "IR Illuminator/Laser Illuminator Support",
    unit: null,
    groupHe: "לייזר",
    required: false,
  },
  {
    key: "anti_vandalism_hardening",
    labelHe: "הקשחה נגד חבלה/פגעי טבע",
    labelEn: "Anti-Vandalism/Weather Hardening",
    unit: null,
    groupHe: "מכניקה וסביבה",
    required: false,
  },
  {
    key: "maintenance_interval",
    labelHe: "מרווח תחזוקה מתוכנן",
    labelEn: "Scheduled Maintenance Interval",
    unit: "חודשים / שעות פעולה",
    groupHe: "בשלות ולוגיסטיקה",
    required: false,
  },
];

const LINE_PARAMS: Record<string, SpecVocabParam[]> = {
  targeting_pods: LINE_PARAMS_TARGETING_PODS,
  mws_eo: LINE_PARAMS_MWS_EO,
  lorop_pods: LINE_PARAMS_LOROP_PODS,
  eo_air_defense_warning: LINE_PARAMS_EO_AIR_DEFENSE_WARNING,
  ball_gimbals_16in: LINE_PARAMS_BALL_GIMBALS_16IN,
  border_long_range_eo: LINE_PARAMS_BORDER_LONG_RANGE_EO,
};

/** `common` alone -- the vocabulary for a dossier with no `product_line` set. */
export function commonVocabulary(): SpecVocabParam[] {
  return COMMON_PARAMS;
}

/** `common` + `vocabulary[line]`, `common` first (concatenation, not merge-by-key -- matches
 * `docs/PLAN_SPEC_VOCABULARY.md` §0's "effective vocabulary" definition exactly). An unknown or
 * null `productLine` falls back to `common` alone, same as the backend's own fallback. */
export function effectiveVocabulary(productLine: string | null | undefined): SpecVocabParam[] {
  if (!productLine) return COMMON_PARAMS;
  const line = LINE_PARAMS[productLine];
  return line ? [...COMMON_PARAMS, ...line] : COMMON_PARAMS;
}

/** `key -> SpecVocabParam` lookup for a dossier's effective vocabulary -- used to resolve a
 * keyed `DossierSpecRow`/`DossierPerformanceRow` to its group/label/required-ness for rendering. */
export function specParamByKey(productLine: string | null | undefined): Map<string, SpecVocabParam> {
  return new Map(effectiveVocabulary(productLine).map((p) => [p.key, p]));
}

/** docs/PLAN_SPEC_VOCABULARY.md §3.4: which vocabulary keys route to the `performance` table (a
 * MEASURED outcome where claimed-vs-demonstrated is a meaningful distinction) vs. `specifications`
 * (everything else) -- §3.4's own text enumerates this exact key list by name. The YAML's own
 * `table:` field is flagged, not yet added (§9 item 1: "designed but not yet added ... whichever
 * lane implements §3.1 first should add it"), so this constant is the interim, kept in sync with
 * that prose by hand. Supersede this (read a real `table:` field instead), don't duplicate it,
 * once the vocabulary file/API carries that field -- this is what lets
 * `DossierSpecTable`/`DossierComparisonView` decide which table a given required key's
 * "לא נמצא במקורות" placeholder belongs in, without rendering it (wrongly) in both. */
export const PERFORMANCE_ROUTED_KEYS = new Set<string>([
  "detection_range_dri",
  "size_to_performance_ratio",
  "adverse_weather_performance",
  "false_alarm_rate",
  "declaration_time",
  "probability_of_detection",
  "revisit_rate",
  "detection_range_small_uas",
  "false_alarm_rate_ad",
  "gsd",
  "standoff_range",
  "dri_at_long_range",
  "slew_rate",
  "stabilization_class_microrad",
]);

export type SpecTableKind = "specifications" | "performance";

/** The effective vocabulary restricted to the keys that belong in `table` -- see
 * `PERFORMANCE_ROUTED_KEYS`'s own doc comment for the interim nature of this split. */
export function vocabularyForTable(
  productLine: string | null | undefined,
  table: SpecTableKind,
): SpecVocabParam[] {
  return effectiveVocabulary(productLine).filter((p) =>
    table === "performance" ? PERFORMANCE_ROUTED_KEYS.has(p.key) : !PERFORMANCE_ROUTED_KEYS.has(p.key),
  );
}

/** Groups an effective vocabulary by `group_he`, in `SPEC_GROUP_ORDER`'s fixed order, each
 * group's own rows kept in vocabulary declaration order (not alphabetical) -- exactly what
 * `docs/PLAN_SPEC_VOCABULARY.md` §5.1 specifies for the grouped spec table. */
export function groupVocabulary(params: SpecVocabParam[]): { groupHe: string; params: SpecVocabParam[] }[] {
  const byGroup = new Map<string, SpecVocabParam[]>();
  for (const p of params) {
    const list = byGroup.get(p.groupHe);
    if (list) list.push(p);
    else byGroup.set(p.groupHe, [p]);
  }
  const ordered: { groupHe: string; params: SpecVocabParam[] }[] = [];
  for (const g of SPEC_GROUP_ORDER) {
    const list = byGroup.get(g);
    if (list && list.length > 0) ordered.push({ groupHe: g, params: list });
  }
  // A future group_he value this file hasn't been synced for yet still renders (never silently
  // drops rows) -- appended after the fixed groups rather than assumed impossible.
  for (const [g, list] of byGroup) {
    if (!SPEC_GROUP_ORDER.includes(g)) ordered.push({ groupHe: g, params: list });
  }
  return ordered;
}
