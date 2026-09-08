"""Typed access to ``config/spec_vocabulary.yaml`` (PD-vocab-extract, docs/PLAN_SPEC_VOCABULARY.md
lane a): the closed, stable-``key`` specification/performance parameter vocabulary that replaces
free-named ``SpecRow.parameter_he``/``PerformanceRow.metric_he`` -- see that plan's own opening
section for the id=1/2/3 drift (the same fact named four different ways, split inconsistently
between the specifications and performance sections) this exists to fix.

Mirrors ``eoa.product_lines.registry``'s read-through-``settings()`` convention (not raw
``yaml.safe_load`` against the filesystem) so ``settings.cache_clear()`` in a test also invalidates
this module's own cache, and a test fixture can override ``Settings(spec_vocabulary={...})``
without touching the filesystem. Every ``key`` is asserted globally unique across ``common`` + all
six product-line blocks the first time the vocabulary is loaded for a given ``settings()`` object --
a duplicate key is a config bug, fail loud, never silently pick one (the plan's own §3.1
requirement).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from eoa.config import settings
from eoa.errors import ConfigError
from eoa.pipeline.text_match import synonym_present

#: The six product_lines.yaml ids config/spec_vocabulary.yaml has its own block for, in the same
#: order the file declares them -- see that file's own header comment.
LINE_BLOCK_KEYS: tuple[str, ...] = (
    "targeting_pods",
    "mws_eo",
    "lorop_pods",
    "eo_air_defense_warning",
    "ball_gimbals_16in",
    "border_long_range_eo",
)

#: The 8 fixed section groups every `group_he` must be one of -- config/spec_vocabulary.yaml's own
#: header comment, repeated here as a standing regression check (docs/PLAN_SPEC_VOCABULARY.md
#: section 7 acceptance check 1).
GROUP_ORDER_HE: tuple[str, ...] = (
    "אופטיקה",
    "חיישנים",
    "לייזר",
    "ייצוב ובקרה",
    "מכניקה וסביבה",
    "ממשקים",
    "ביצועי מערכת",
    "בשלות ולוגיסטיקה",
)

_TABLE_VALUES = {"specifications", "performance"}


@dataclass(frozen=True)
class SpecParam:
    """One ``config/spec_vocabulary.yaml`` parameter entry -- field-by-field contract documented in
    that file's own header comment; not duplicated out of sync here."""

    key: str
    label_he: str
    label_en: str
    unit: str | None
    value_type: str
    enum_values: list[str] | None
    synonyms: list[str]
    group_he: str
    required: bool
    notes_he: str
    #: "specifications" | "performance" -- which of ProductDossierOut's two row lists this key's
    #: extracted value belongs in (section 3.4's routing rule; added to the YAML post-freeze, see
    #: that file's own header comment for the field and section 9 item 1 for why it wasn't there
    #: from the start).
    table: str


def _parse_param(row: dict, *, block: str) -> SpecParam:
    key = row.get("key")
    if not key:
        raise ConfigError(f"config/spec_vocabulary.yaml: a parameter in {block!r} is missing 'key'")
    table = row.get("table") or "specifications"
    if table not in _TABLE_VALUES:
        raise ConfigError(f"config/spec_vocabulary.yaml: key {key!r} has invalid table {table!r}")
    group_he = str(row.get("group_he") or "")
    if group_he not in GROUP_ORDER_HE:
        raise ConfigError(f"config/spec_vocabulary.yaml: key {key!r} has unknown group_he {group_he!r}")
    value_type = str(row.get("value_type") or "text")
    enum_values = row.get("enum_values")
    if value_type == "enum" and not enum_values:
        raise ConfigError(f"config/spec_vocabulary.yaml: key {key!r} is value_type=enum with no enum_values")
    return SpecParam(
        key=str(key),
        label_he=str(row.get("label_he") or ""),
        label_en=str(row.get("label_en") or ""),
        unit=row.get("unit"),
        value_type=value_type,
        enum_values=[str(v) for v in enum_values] if enum_values else None,
        synonyms=[str(s) for s in (row.get("synonyms") or [])],
        group_he=group_he,
        required=bool(row.get("required", False)),
        notes_he=str(row.get("notes_he") or ""),
        table=table,
    )


@lru_cache(maxsize=1)
def _raw_vocabulary_cached(cache_token: int) -> dict[str, tuple[SpecParam, ...]]:
    """``cache_token`` is ``id(settings())`` -- the same "invalidate on a new Settings object"
    convention ``eoa.product_lines.registry._defs_cached`` already uses, so
    ``settings.cache_clear()`` (the standard way tests/CLI reload config) transparently invalidates
    this cache too, without a dedicated ``clear_cache()`` wired into every test fixture."""
    raw = settings().spec_vocabulary
    if not raw:
        raise ConfigError("config/spec_vocabulary.yaml is missing or empty")
    out: dict[str, tuple[SpecParam, ...]] = {}
    seen_keys: dict[str, str] = {}
    for block_name in ("common", *LINE_BLOCK_KEYS):
        rows = raw.get(block_name) or []
        params: list[SpecParam] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            param = _parse_param(row, block=block_name)
            if param.key in seen_keys:
                raise ConfigError(
                    f"config/spec_vocabulary.yaml: duplicate key {param.key!r} in block "
                    f"{block_name!r} (already used in block {seen_keys[param.key]!r})"
                )
            seen_keys[param.key] = block_name
            params.append(param)
        out[block_name] = tuple(params)
    return out


def load_vocabulary() -> dict[str, list[SpecParam]]:
    """Raw ``common`` + one list per product-line block, in ``config/spec_vocabulary.yaml``'s own
    declaration order (the import-time-equivalent uniqueness assertion runs the first time this
    loads for a given ``settings()`` object -- see :func:`_raw_vocabulary_cached`)."""
    raw = _raw_vocabulary_cached(id(settings()))
    return {block: list(params) for block, params in raw.items()}


def effective_vocabulary(product_line: str | None) -> list[SpecParam]:
    """``common`` followed by ``vocabulary[product_line]`` -- concatenation, not merge-by-key (no
    key collides between ``common`` and any line block, or between two line blocks -- asserted at
    load time). A ``product_line`` that is ``None``, empty, or not one of the six known line ids
    falls back to ``common`` alone (the plan's documented behavior for a dossier with no product
    line set)."""
    vocab = load_vocabulary()
    params = list(vocab.get("common") or [])
    if product_line and product_line in LINE_BLOCK_KEYS:
        params.extend(vocab.get(product_line) or [])
    return params


def param_by_key(product_line: str | None) -> dict[str, SpecParam]:
    """The effective vocabulary for ``product_line``, indexed by ``key`` -- the join eoa.dossier.
    extract/diff use for key validation, label normalization, and table routing."""
    return {p.key: p for p in effective_vocabulary(product_line)}


def all_params_by_key() -> dict[str, SpecParam]:
    """Every vocabulary key across ``common`` + all six product-line blocks, indexed by ``key`` --
    a flat global map, safe because every ``key`` is globally unique (asserted at load time). Used
    where the caller has a ``key`` in hand but not the product-line context that produced it (e.g.
    ``eoa.dossier.diff``, which reasons about a stored dossier's own already-keyed rows without
    needing to re-derive that dossier's product line)."""
    vocab = load_vocabulary()
    out: dict[str, SpecParam] = {}
    for params in vocab.values():
        for p in params:
            out[p.key] = p
    return out


def vocabulary_prompt_block_he(params: list[SpecParam]) -> str:
    """One numbered line per parameter -- ``key | label_he (label_en) | value_type [enum: ...] |
    unit`` -- fed verbatim into the extraction prompt (section 3.3) as the enumerated vocabulary
    list the model is asked to fill from, never invent/reword."""
    lines = []
    for p in params:
        bits = [p.key, f"{p.label_he} ({p.label_en})" if p.label_en else p.label_he]
        type_bit = p.value_type
        if p.value_type == "enum" and p.enum_values:
            type_bit = f"enum: {', '.join(p.enum_values)}"
        bits.append(type_bit)
        bits.append(p.unit or "—")
        lines.append(" | ".join(bits))
    return "\n".join(lines)


def match_key_by_synonym(text: str, product_line: str | None) -> str | None:
    """The first vocabulary key (effective vocabulary for ``product_line``, declaration order)
    whose own ``label_he``/``label_en``/``synonyms`` matches somewhere in ``text`` -- the reused
    matcher (section 3.2), used only as a non-destructive QA signal by eoa.dossier.extract (never to
    silently reclassify a row the model itself decided belonged in ``other_specifications``)."""
    if not text or not text.strip():
        return None
    for param in effective_vocabulary(product_line):
        candidates = [param.label_he, param.label_en, *param.synonyms]
        if any(synonym_present(text, text, c) for c in candidates if c):
            return param.key
    return None


__all__ = [
    "GROUP_ORDER_HE",
    "LINE_BLOCK_KEYS",
    "SpecParam",
    "all_params_by_key",
    "effective_vocabulary",
    "load_vocabulary",
    "match_key_by_synonym",
    "param_by_key",
    "vocabulary_prompt_block_he",
]
