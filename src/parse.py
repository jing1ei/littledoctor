"""Reading the *text* of a report: dates, rows, units, impressions, conclusions.

This module knows nothing about files or the knowledge base. It takes a line or
a page of text — typed, exported, OCR'd or spoken by a vision model — and turns
it into candidate rows. Every real-world report shape it has met is represented
here as a test case in ``knowledge/parser_fixtures.yaml``; run

    python3 littledocter.py selftest

after touching anything below.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

# ── dates ────────────────────────────────────────────────────────────────────

MONTH_WORDS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _century(y: str) -> str:
    """'24' -> '2024', '98' -> '1998'. Four-digit years pass through."""
    if len(y) > 2:
        return y
    n = int(y)
    return str(2000 + n if n <= 79 else 1900 + n)


def _month_word(w: str) -> str | None:
    m = MONTH_WORDS.get(w[:3].lower())
    return str(m) if m else None


def _dmy(a: str, b: str, y: str) -> tuple[str, str, str]:
    """08/22/2024 and 22/08/2024 both read correctly: a day above 12 decides."""
    return (y, b, a) if int(a) > 12 and int(b) <= 12 else (y, a, b)


# Ordered: the least ambiguous shapes first. Each rule returns (year, month, day)
# as strings; ``None`` means "not stated" and falls back to the 1st.
DATE_RULES = [
    # 2024-08-22 · 2024年8月22日 · 2024.08.22 · 2024/8/22
    (re.compile(r"(19\d{2}|20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})"),
     lambda m: (m.group(1), m.group(2), m.group(3))),
    # 20240822 — common in filenames and machine exports
    (re.compile(r"(?<!\d)(19|20)(\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)"),
     lambda m: (m.group(1) + m.group(2), m.group(3), m.group(4))),
    # 22 Aug 2024 · 22 August 2024
    (re.compile(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?[\s\-]*([A-Za-z]{3,9})\.?,?\s*(19\d{2}|20\d{2})\b"),
     lambda m: (m.group(3), _month_word(m.group(2)), m.group(1))),
    # Aug 22, 2024 · August 22 2024
    (re.compile(r"\b([A-Za-z]{3,9})\.?\s*(\d{1,2})\s*(?:st|nd|rd|th)?,?\s*(19\d{2}|20\d{2})\b"),
     lambda m: (m.group(3), _month_word(m.group(1)), m.group(2))),
    # 08/22/2024 · 22-08-2024
    (re.compile(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(19\d{2}|20\d{2})(?!\d)"),
     lambda m: _dmy(m.group(1), m.group(2), m.group(3))),
    # 24-08-22 — a two-digit year, still used by older hospital systems
    (re.compile(r"(?<![\d.])(\d{2})\s*([-/.])\s*(\d{1,2})\s*\2\s*(\d{1,2})(?![\d.])"),
     lambda m: (_century(m.group(1)), m.group(3), m.group(4))),
    # 2024年8月 · 2024-08
    (re.compile(r"(19\d{2}|20\d{2})\s*[-/年.]\s*(\d{1,2})\s*月?"),
     lambda m: (m.group(1), m.group(2), None)),
    # a bare year, the last resort
    (re.compile(r"\b(19\d{2}|20\d{2})\b"), lambda m: (m.group(1), None, None)),
]
DATE_HINT = re.compile(
    r"(报告[日时]期|检验日期|采样[日时]间|采集[日时]间|体检日期|检查日期|就诊日期|"
    r"报告时间|签发日期|出报告|日期|date|collected|reported|study date)"
    r"[^\n0-9]{0,12}([^\n]{4,24})", re.I)


def _mk_date(y: str | None, m: str | None, d: str | None) -> str | None:
    try:
        yy, mm, dd = int(y), int(m) if m else 1, int(d) if d else 1
        if not (1930 <= yy <= 2100 and 1 <= mm <= 12 and 1 <= dd <= 31):
            return None
        return date(yy, mm, dd).isoformat()
    except Exception:
        return None


def _scan_date(text: str) -> tuple[str | None, int]:
    """First readable date in ``text``, with how precise it is.

    Precision matters: ``2024`` alone becomes 2024-01-01, which would misplace a
    report by up to a year, so the caller can keep looking for something better.
    """
    for pat, read in DATE_RULES:
        for m in pat.finditer(text):
            try:
                y, mo, d = read(m)
                got = _mk_date(y, mo, d)
            except Exception:
                got = None
            if got:
                return got, 3 if d else 2 if mo else 1
    return None, 0


def find_date(text: str) -> str | None:
    """The report's own date, preferring an explicitly labelled one."""
    flat = unicodedata.normalize("NFKC", text or "")
    best, prec = None, 0
    hint = DATE_HINT.search(flat)
    if hint:
        # the label's own window first, then a wider look around it — a hint like
        # "Collected" can push the month out of the narrow capture
        for window in (hint.group(2), flat[hint.start():hint.start() + 80]):
            got, p = _scan_date(window)
            if p > prec:
                best, prec = got, p
            if prec == 3:
                return best
    got, p = _scan_date(flat)
    return got if p > prec else best


def date_from_name(stem: str) -> str | None:
    return find_date(str(stem).replace("_", "-"))


# ── words that mean yes and no ───────────────────────────────────────────────

QUALITATIVE = {
    "阴性": "negative", "negative": "negative", "neg": "negative", "-": "negative",
    "(-)": "negative", "未见": "negative", "未检出": "negative", "阴": "negative",
    "阳性": "positive", "positive": "positive", "pos": "positive", "+": "positive",
    "(+)": "positive", "++": "positive", "+++": "positive", "阳": "positive",
    "正常": "normal", "normal": "normal", "未见异常": "normal", "无异常": "normal",
    "规律": "normal", "不规律": "abnormal", "异常": "abnormal", "abnormal": "abnormal",
}

ABNORMAL_WORDS = [
    "脂肪肝", "结节", "结石", "息肉", "囊肿", "斑块", "增厚", "钙化", "肿大", "扩张",
    "反流", "狭窄", "积液", "肥厚", "萎缩", "糜烂", "溃疡", "疝", "占位", "肌瘤",
    "增生", "低回声", "高回声", "不均", "阳性", "异常", "偏高", "偏低", "早搏", "炎症",
    "窦性心律不齐", "纤维化", "钙乳", "牵拉", "毛玻璃", "实性", "磨玻璃", "龋", "牙结石",
    "牙龈炎", "近视", "散光", "缺失", "受限", "变窄", "迂曲", "紊乱", "缺血", "梗死",
]
ABNORMAL_WORDS_EN = [
    "fatty liver", "steatosis", "nodule", "cyst", "polyp", "stone", "calculus",
    "calcification", "plaque", "thicken", "dilat", "reflux", "stenos", "effusion",
    "hypertroph", "atroph", "erosion", "ulcer", "hernia", "mass", "lesion",
    "fibrosis", "inflammation", "inflamed", "gingivitis", "caries", "hyperplasia",
    "hypoechoic", "hyperechoic", "heterogene", "positive", "abnormal", "elevated",
    "ectopic beat", "arrhythmia", "opacity", "ground glass", "suspicious",
    "irregular", "myopia", "astigmatism", "ischemi", "infarct",
]
RADS_RE = re.compile(r"\b(?:ti|bi)[\s\-]?rads\s*[:\-]?\s*([0-6])", re.IGNORECASE)
NORMAL_WORDS = [
    "未见异常", "未见明显异常", "无异常", "正常", "阴性", "未见明确", "未见明显", "未见",
    "清晰", "光滑", "大致正常", "未见占位", "未见结节", "未发现", "无殊", "normal",
    "unremarkable", "no abnormalit", "negative", "clear", "within normal",
    "nothing abnormal", "no evidence of", "nilm", "no significant",
]
NEGATED = re.compile(r"(未见|未发现|未闻及|无)(明显|明确)?[^，。；;,、]{0,8}")
NEGATED_EN = re.compile(
    r"\b(no|without|free of|negative for|absent|denies)\b[^,.;:]{0,40}", re.IGNORECASE)


QUAL_CORE = re.compile(r"(阴性|阳性|未检出|未见异常|无异常|正常|negative|positive|normal|abnormal|异常)",
                       re.IGNORECASE)


def parse_qualitative(tok) -> str | None:
    t = unicodedata.normalize("NFKC", str(tok)).strip().lower()
    if t in QUALITATIVE:
        return QUALITATIVE[t]
    t2 = re.sub(r"[（）()\s]", "", t)
    if t2 in QUALITATIVE:
        return QUALITATIVE[t2]
    core = QUAL_CORE.search(t)
    if core:
        # only decoration may surround the word, otherwise a whole sentence
        # ("肝、胆、脾、胰未见明显异常。") would be mistaken for a cell value
        residue = re.sub(r"[（）()\s+\-±*↑↓。,，;；]", "", t)
        if residue == core.group(1).lower() or residue in QUALITATIVE:
            return QUALITATIVE.get(core.group(1).lower())
    return None


def canonical_qualitative(tok) -> str:
    """'阳性(+)' -> '阳性'. Keeps the report's own word, drops decoration."""
    t = unicodedata.normalize("NFKC", str(tok)).strip()
    inner = re.sub(r"[（(][^)）]*[)）]", "", t).strip()
    if inner and parse_qualitative(inner) == parse_qualitative(t):
        return inner
    return t


def classify_impression(text: str) -> str:
    """abnormal / normal / unclear, with negations honoured."""
    t = str(text or "")
    stripped = NEGATED.sub("", t)
    negated_en = NEGATED_EN.sub("", stripped)
    stripped_en = negated_en.lower()
    low = t.lower()
    rads = RADS_RE.search(low)
    if rads and int(rads.group(1)) >= 3:
        return "abnormal"
    if any(w in stripped for w in ABNORMAL_WORDS):
        return "abnormal"
    if any(w in stripped_en for w in ABNORMAL_WORDS_EN):
        return "abnormal"
    if any(w in low for w in NORMAL_WORDS):
        return "normal"
    if negated_en != stripped or stripped != t:
        return "normal"
    if rads:
        return "normal"
    return "unclear"


# ── one row of a lab table ───────────────────────────────────────────────────

NUM = r"-?\d+(?:[.,]\d+)?(?:[eE]-?\d+)?"
VALUE_RE = re.compile(rf"^[<>≤≥]?\s*{NUM}$")
GLUED_RE = re.compile(rf"^([<>≤≥]?\s*{NUM})\s*([a-zA-Zµμ%°/×^*().\d\-]{{1,18}})$")
BP_RE = re.compile(r"^(\d{2,3})\s*/\s*(\d{2,3})$")
RANGE_RE = re.compile(rf"^[<>≤≥(\[]?\s*{NUM}\s*(?:[-~—－–]\s*{NUM})?\s*[)\]]?$")
UNIT_RE = re.compile(r"^[a-zA-Zµμ%°/×^*.()\d\-]{1,18}$")
# a token we are willing to treat as a unit *before* the value; deliberately
# narrow, otherwise the second word of a name ("Blood pressure") gets eaten
KNOWN_UNITS = {
    "%", "mg", "g", "kg", "ug", "µg", "μg", "ng", "pg", "mmol", "umol", "µmol",
    "μmol", "nmol", "pmol", "mol", "l", "ml", "dl", "u", "iu", "miu", "uiu",
    "mmhg", "kpa", "cm", "mm", "m", "kg/m2", "bpm", "s", "sec", "min", "fl",
    "mosm", "ratio", "index", "score",
}
UNIT_STRICT = re.compile(r"^(?=.{1,18}$)(?=.*[/%^×*·\d])[a-zA-Zµμ%°/×^*.()\d\-]+$")
FLAG_RE = re.compile(r"^(H|L|HH|LL|↑|↑↑|↓|↓↓|⇑|⇓|\*|!|高|低|偏高|偏低|异常|阳性)$")
TRAILING_FLAG = re.compile(r"[↑↓⇑⇓*!HL]+$")
LEADING_FLAG = re.compile(r"^[↑↓⇑⇓*!+]+")
BULLET = re.compile(r"^\s*(?:[0-9]{1,2}[.、)）]|[•·▪\-–—o○*#]|\([0-9]{1,2}\))\s*")
SECTION = re.compile(r"^[\s【\[（(=＝\-—─═*#·]+$")
NOISE_LINE = re.compile(
    r"(签名|医师|审核|打印|页\s*/|第\s*\d+\s*页|条形码|条码|样本号|门诊号|住院号|床号|"
    r"科室|地址|电话|传真|www\.|http|备注仅供|仅供参考|report\s*id|barcode|specimen no)",
    re.IGNORECASE)


@dataclass
class Row:
    """A candidate measurement, still in the report's own words."""
    name: str
    value: str
    unit: str = ""
    ref: str = ""
    flag: str = ""
    note: str = ""
    fields: list[str] = field(default_factory=list)


def clean_line(line: str) -> str:
    s = unicodedata.normalize("NFKC", str(line or ""))
    s = s.replace("\u3000", "  ").replace("\ufeff", "")
    s = re.sub(r"[，、]\s*$", "", s)
    return s.strip()


def parse_number(tok) -> float | None:
    """A number, even when the report glued a flag or a thousands comma to it."""
    t = unicodedata.normalize("NFKC", str(tok)).strip()
    t = LEADING_FLAG.sub("", TRAILING_FLAG.sub("", t)).strip()
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)          # 1,234 -> 1234
    t = t.replace(",", ".") if re.fullmatch(r"-?\d+,\d{1,2}", t) else t   # 5,6 -> 5.6
    if not VALUE_RE.match(t):
        return None
    try:
        return float(re.sub(r"[<>≤≥\s]", "", t))
    except ValueError:
        return None


def _looks_like_value(tok: str) -> bool:
    t = unicodedata.normalize("NFKC", tok).strip()
    bare = LEADING_FLAG.sub("", TRAILING_FLAG.sub("", t)).strip()
    return bool(VALUE_RE.match(bare) or BP_RE.match(t)
                or parse_qualitative(t) is not None)


def _split_glued(tok: str) -> tuple[str, str]:
    """'23U/L' -> ('23', 'U/L');  '5.42' -> ('5.42', '')."""
    t = unicodedata.normalize("NFKC", tok).strip()
    if _looks_like_value(t):
        return t, ""
    m = GLUED_RE.match(t)
    if m and not RANGE_RE.match(t):
        num, unit = m.group(1).strip(), m.group(2).strip()
        if _looks_like_value(num) and not unit.replace(".", "").isdigit():
            return num, unit
    return t, ""


def tokenise(line: str) -> list[str]:
    """Columns first (tabs, pipes, wide gaps), then plain whitespace.

    When the line already came in columns we only split further on colons — a
    column like ``谷丙转氨酶 ALT`` is one name, not two tokens.
    """
    s = clean_line(line)
    if "\t" in s:
        parts, columnar = s.split("\t"), True
    elif "|" in s:
        parts, columnar = s.split("|"), True
    elif re.search(r"\s{2,}", s):
        parts, columnar = re.split(r"\s{2,}", s), True
    else:
        parts, columnar = [s], False
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        splitter = r"[:：]" if columnar else r"[:：]|\s+"
        bits = [b.strip() for b in re.split(splitter, p) if b.strip()]
        out.extend(bits if len(bits) > 1 else [p])
    return [o.strip() for o in out if o.strip()]


def _segmentations(s: str):
    """Ways this line might be carrying more than one measurement."""
    yield [s]
    if re.search(r"[;；]|[，、]|(?<!\d),(?!\d)", s):
        yield re.split(r"[;；]|[，、]|(?<!\d),(?!\d)", s)
    if re.search(r"\s{2,}", s):
        yield re.split(r"\s{2,}", s)
    if len(re.findall(r"[:：]", s)) >= 2:
        yield re.split(r"\s+(?=[^\s:：]{1,20}[:：])", s)


def _row_from_segment(seg: str) -> Row | None:
    seg = BULLET.sub("", clean_line(seg)).strip()
    if len(seg) < 2:
        return None
    toks = tokenise(seg)
    if len(toks) < 2:
        return None
    vi, value, glued_unit = None, "", ""
    for i in range(1, len(toks)):
        num, unit = _split_glued(toks[i])
        if _looks_like_value(num):
            vi, value, glued_unit = i, num, unit
            break
    if vi is None:
        return None
    name = LEADING_FLAG.sub("", " ".join(toks[:vi])).strip(" :：·.")
    lead = LEADING_FLAG.match(value)
    if lead:                                   # '*52' — the star is a flag
        value = value[lead.end():].strip()
    if not name or len(name) > 80:
        return None
    unit, ref, flag = glued_unit, "", ""
    # some Chinese lab exports print 单位 in the column before 结果
    if not unit and vi >= 2 and (UNIT_STRICT.match(toks[vi - 1])
                                 or toks[vi - 1].lower() in KNOWN_UNITS) \
            and not RANGE_RE.match(toks[vi - 1]):
        unit = toks[vi - 1]
        name = " ".join(toks[:vi - 1]).strip(" :：·.") or name
    for tok in toks[vi + 1:]:
        t = unicodedata.normalize("NFKC", tok).strip()
        if FLAG_RE.match(t):
            flag = flag or t
            continue
        if RANGE_RE.match(t) or re.search(rf"{NUM}\s*[-~—－–]\s*{NUM}", t) \
                or re.match(r"^[<>≤≥]", t):
            ref = ref or t
            continue
        if not unit and UNIT_RE.match(t) and not t.isdigit():
            unit = t
            continue
    stuck = unicodedata.normalize("NFKC", toks[vi]).strip()
    m = TRAILING_FLAG.search(stuck) or LEADING_FLAG.match(stuck)
    if m and not flag:
        flag = m.group(0)
    if parse_qualitative(value) is not None:
        value = canonical_qualitative(value)
    return Row(name=name, value=value, unit=unit, ref=ref, flag=flag, fields=toks)


def parse_rows(line: str) -> list[Row]:
    """Turn one physical line into 0..n candidate rows, in the report's words.

    Several segmentations are attempted and the richest one wins, so a table row
    (``ALT  52  U/L  9-50``) and a vitals line (``身高 168cm  体重 71.4kg``) both
    read correctly without the caller having to know which is which.
    """
    raw = clean_line(line)
    if len(raw) < 2 or SECTION.match(raw) or NOISE_LINE.search(raw):
        return []
    best: list[Row] = []
    for segs in _segmentations(raw):
        rows = [r for r in (_row_from_segment(p) for p in segs if p.strip()) if r]
        if len(rows) > len(best):
            best = rows
    return best


# ── imaging and conclusions ──────────────────────────────────────────────────

MODALITY = [
    ("pathology", ["病理", "活检", "细胞学", "patholog", "biopsy", "cytolog", "tct", "液基", "hpv"]),
    ("mammography", ["钼靶", "mammograph"]),
    ("dexa", ["骨密度", "dexa", "dxa", "bone densit"]),
    ("ecg", ["心电图", "ecg", "ekg", "electrocardiogram", "holter", "动态心电"]),
    ("endoscopy", ["胃镜", "肠镜", "内镜", "endoscop", "gastroscop", "colonoscop"]),
    ("ultrasound", ["超声", "彩超", "b超", "彩色多普勒", "ultrasound", "sonograph", "doppler"]),
    ("mri", ["mri", "磁共振", "核磁", "magnetic resonance"]),
    ("ct", ["ct", "计算机断层", "computed tomograph", "低剂量"]),
    ("xray", ["x线", "x光", "胸片", "dr片", "平片", "radiograph", "chest x"]),
    ("fundus", ["眼底", "fundus", "oct", "视野"]),
    ("dental", ["口腔", "牙", "dental", "periodont"]),
    ("spirometry", ["肺功能", "spirometr", "通气功能"]),
]
# short latin keys must not match inside another word ('ct' inside 'TCT')
_MOD_RE = [
    (name, [re.compile(rf"(?<![a-z]){re.escape(k)}(?![a-z])" if k.isascii() and len(k) <= 4
                       else re.escape(k), re.IGNORECASE) for k in keys])
    for name, keys in MODALITY
]


HEADER_LINE = re.compile(
    r"(报告单|体检报告|检查报告|健康管理中心|体检中心|检验科|报告编号|姓名|性别|年龄|"
    r"就诊卡|医疗机构|医院\s*$|report (form|sheet|id)|patient name|date of birth)",
    re.IGNORECASE)

CONCLUSION_HEAD = re.compile(
    r"(检查所见|影像所见|超声所见|所见|结论|小结|诊断|印象|意见|建议|提示|总结|复查建议|"
    r"impression|conclusion|diagnos|finding|summary|assessment|recommend|comment)"
    r"\s*[:：]?\s*", re.IGNORECASE)

# words a report uses in its own conclusions, mapped to the system they touch
CONCLUSION_SYSTEM = {
    "liver": ["脂肪肝", "肝功能", "转氨酶", "肝内", "胆囊", "胆管", "fatty liver", "hepatic",
              "gallbladder", "alt", "ast", "steatosis"],
    "cardiovascular": ["血脂", "胆固醇", "甘油三酯", "动脉", "颈动脉", "斑块", "血压", "心律",
                       "心电", "早搏", "lipid", "cholesterol", "carotid", "blood pressure",
                       "arrhythmia", "ecg"],
    "metabolic": ["血糖", "糖化", "糖耐量", "胰岛素", "超重", "肥胖", "体重", "腰围", "代谢",
                  "glucose", "hba1c", "insulin", "obes", "overweight", "waist"],
    "kidney": ["肾", "尿蛋白", "肌酐", "尿酸", "renal", "kidney", "creatinine", "proteinuria"],
    "thyroid": ["甲状腺", "thyroid", "tsh", "ti-rads"],
    "hematology": ["血红蛋白", "贫血", "铁", "血常规", "白细胞", "血小板", "anemia", "anaemia",
                   "ferritin", "haemoglobin", "hemoglobin", "platelet"],
    "gastrointestinal": ["胃", "肠", "幽门螺", "反流", "息肉", "gastr", "colon", "pylori",
                         "reflux", "polyp"],
    "respiratory": ["肺", "支气管", "胸部", "胸片", "pulmonary", "lung", "bronch",
                    "肺结节", "pulmonary nodule"],
    "bone": ["骨密度", "骨质", "腰椎", "颈椎", "脊柱", "bone", "osteop", "spine", "lumbar"],
    "hormonal": ["子宫", "卵巢", "乳腺", "月经", "hpv", "宫颈", "uterus", "ovar", "breast",
                 "cervi", "menstru"],
    "nutrition": ["维生素", "叶酸", "vitamin", "folate", "b12"],
    "inflammation": ["炎症", "crp", "免疫", "过敏", "inflamm", "immun", "allerg"],
    "senses_dental": ["牙", "龋", "牙龈", "视力", "近视", "眼底", "听力", "dental", "caries",
                      "gingiv", "vision", "myopia", "hearing", "fundus"],
    "neuro_sleep": ["睡眠", "焦虑", "抑郁", "情绪", "失眠", "sleep", "anxiet", "depress",
                    "insomnia", "mood"],
    "cancer_screening": ["筛查", "肿瘤标志", "screening", "tumour marker", "tumor marker"],
}


def detect_modality(text: str) -> str | None:
    low = unicodedata.normalize("NFKC", str(text or "")).lower()
    for name, pats in _MOD_RE:
        if any(p.search(low) for p in pats):
            return name
    return None


def systems_in(text: str) -> list[str]:
    low = unicodedata.normalize("NFKC", str(text or "")).lower()
    out = []
    for sid, keys in CONCLUSION_SYSTEM.items():
        if any(k in low for k in keys):
            out.append(sid)
    return out


def is_impression_line(line: str) -> bool:
    """A prose line that reads like a report's own finding, heading or not.

    ``腹部超声：肝内回声稍增强，考虑轻度脂肪肝`` carries no ``结论:`` label but is
    exactly the sentence a person wants to see quoted back to them.
    """
    s = clean_line(line)
    if len(s) < 8 or NOISE_LINE.search(s) or SECTION.match(s) or HEADER_LINE.search(s):
        return False
    if parse_rows(s):
        return False
    body = re.sub(r"^[【\[（(]?[^】\]）)：:]{0,18}[】\]）)]?\s*[:：]?\s*", "", s) or s
    if len(body) < 6:
        return False
    return bool(detect_modality(s) or systems_in(s)
                or classify_impression(s) == "abnormal")


def parse_conclusions(text: str, max_items: int = 40) -> list[dict]:
    """What the report itself concluded — the sentences a human wrote or dictated.

    Kept verbatim: these are evidence, not data, and the page shows them as
    quotes next to the system they touch.
    """
    lines = [clean_line(l) for l in str(text or "").splitlines()]
    out: list[dict] = []
    collecting = False
    for raw in lines:
        if not raw:
            collecting = False
            continue
        head = CONCLUSION_HEAD.search(raw)
        if head and head.start() <= 6:
            tail = raw[head.end():].strip(" :：-—")
            collecting = True
            if len(tail) >= 4:
                out.append({"text": tail, "systems": systems_in(tail),
                            "status": classify_impression(tail)})
            continue
        if collecting:
            body = BULLET.sub("", raw).strip()
            # a new table row ends the prose block
            if not body or len(body) < 4 or parse_rows(raw):
                collecting = False
            else:
                out.append({"text": body, "systems": systems_in(body),
                            "status": classify_impression(body)})
                continue
        if is_impression_line(raw):
            body = BULLET.sub("", raw).strip()
            out.append({"text": body, "systems": systems_in(body),
                        "status": classify_impression(body)})
        if len(out) >= max_items:
            break
    # de-duplicate while keeping order
    seen, uniq = set(), []
    for c in out:
        k = c["text"][:80]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    return uniq[:max_items]
