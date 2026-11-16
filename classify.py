"""Classify a job by its title: the level (how senior) and the role types (which field).

The two are separate. `level()` gives Intern, Junior or Not specified. `role_types()` gives every type the title fits,
or Other when none. Both are driven by data (the patterns below), so a new role type is one entry in `ROLE_TYPES` and a
new keyword is one more string in a type's tuple. `INTERN`, `JUNIOR` and `PM_CORE` are the patterns the old digest
and the dashboard used before this module existed; they live here now, unchanged.
"""

import re
from collections import Counter
from collections.abc import Iterable

PM_CORE = re.compile(r"product|\bPM\b|\bAPM\b|מוצר", re.I)
# Intern wins over Junior when a title matches both.
INTERN = re.compile(r"intern\b|internship|student|סטודנט", re.I)
JUNIOR = re.compile(r"junior|\bjr\b|graduate|entry|trainee", re.I)

INTERN_LEVEL = "Intern"
JUNIOR_LEVEL = "Junior"
NOT_SPECIFIED = "Not specified"
LEVELS = (INTERN_LEVEL, JUNIOR_LEVEL, NOT_SPECIFIED)

OTHER = "Other"

# The words before "engineer" that make a title DevOps & SRE and keep it out of R&D, each with the lookbehinds that
# cancel it ("Data Platform Engineer" is Data & AI, "IT Infrastructure Engineer" is IT, "Mobile-Cross-Platform
# Engineer" is neither). Platform, infrastructure and cloud only count right before "engineer": alone they are too loose
# ("Cloud Solutions Architect", "Customer Platform Manager"). `_devops_engineer` turns the table into the DevOps & SRE
# patterns and `_not_before` into the lookbehinds that keep R&D's generic "engineer" pattern off the same titles.
_DEVOPS_ENGINEER_PREFIXES: dict[str, tuple[str, ...]] = {
    "devops": (),
    "devsecops": (),
    "sre": (),
    "site reliability": (),
    "site-reliability": (),
    "platform": ("data ", "cross ", "-"),
    "infrastructure": ("data ", "it ", "simulation "),
    "infra": ("data ", "it ", "simulation "),
    "cloud": (),
    "release": (),
    "build": (),
    "ci/cd": (),
    "kubernetes": (),
    "k8s": (),
    "production": (),
}


_WORD_START = "(?<![a-z])"  # the prefix starts a word, in both builders: "Multicloud Engineer" is neither type's match


def _not_before(table: dict[str, tuple[str, ...]]) -> str:
    """Lookbehinds that stop a pattern from matching right after one of the prefixes (unless an exception word is
    before it): "(?<!(?<!data )platform )(?<!devops )" and so on."""
    parts = (_WORD_START + "".join(f"(?<!{other})" for other in others) + prefix for prefix, others in table.items())
    return "".join(f"(?<!{part} )" for part in parts)


def _devops_engineer(table: dict[str, tuple[str, ...]]) -> str:
    """One alternation: "<prefix> engineer" for every prefix, minus the exception words."""
    parts = (
        _WORD_START + "".join(f"(?<!{other})" for other in others) + rf"{re.escape(prefix)} engineer"
        for prefix, others in table.items()
    )
    return "|".join(parts)


# Role types in display order. A job belongs to every type whose patterns match its title, and to Other when none does.
# R&D covers, for example, software, hardware, QA and algorithms. DevOps and SRE titles are their own type, not R&D.
# Acronyms that are also common words (IT, AI, ML, BI, UI, BD) use (?-i:...) so they only match as written; the other
# acronyms match in any case.
ROLE_TYPES: dict[str, tuple[str, ...]] = {
    # PM_CORE without its loose "product" substring, which also catches "Production" and "Productivity".
    "Product": (r"\bproduct(?!ion|ivity)|\bPM\b|\bAPM\b|מוצר",),
    "R&D": (
        r"\bR&D\b",
        r"software",
        r"hardware",
        r"developer|\bdev\b",
        # Generic "engineer", but not "Sales Engineer" and the like, nor the DevOps & SRE titles ("Platform Engineer").
        rf"(?<!sales )(?<!solution )(?<!solutions )(?<!support )(?<!success ){_not_before(_DEVOPS_ENGINEER_PREFIXES)}"
        r"\bengineer(?:ing)?",
        r"\bQA\b|\bSDET\b|quality assurance|test automation|verification|validation",
        r"algorithm|firmware|embedded|\bFPGA\b|\bASIC\b|\bRTL\b|\bDFT\b|silicon|chip\b|\bVLSI\b",
        r"full[ -]?stack|back[ -]?end|front[ -]?end|\bmobile\b|\bios\b|android",
        r"tech(nical)? lead|cyber|security (researcher|analyst)|(?<!market )researcher|research scientist",
        r"מפתח|הנדסה|מהנדס",
    ),
    # DevOps and SRE titles are only this type (no R&D), unless another pattern of R&D matches the title too.
    "DevOps & SRE": (
        r"dev ?sec ?ops|dev ?ops|\bsre\b|site reliability|\bci ?/ ?cd\b",
        _devops_engineer(_DEVOPS_ENGINEER_PREFIXES),
    ),
    "Data & AI": (
        r"\bdata\b(?! cent)",
        r"machine learning|deep learning|(?-i:\bML\b)|(?-i:\bAI\b)|\bNLP\b|\bLLM\b|computer vision|\bmlops\b",
        r"analytics|(?-i:\bBI\b)|business intelligence|data scien",
    ),
    "Design": (
        r"designer|\bUX\b|(?-i:\bUI\b)|\bgraphic|illustrat|\bart director|creative director",
        r"\bmotion (design|graphic)|animat",
        r"design lead|head of design|product design|עיצוב|מעצב",
    ),
    "IT": (
        r"(?-i:\bIT\b)|sys ?admin|system administrator|help ?desk|network (admin|engineer)|it support",
        r"desktop support|\bIT infrastructure|\bit (manager|specialist|technician)|מערכות מידע|תמיכה טכנית",
    ),
    "Finance": (
        r"financ|account(ant|ing)\b|bookkeep|(?<!motor )controller|\bFP&A\b|payroll|treasury|\btax\b|audit",
        r"accounts (payable|receivable)|procurement|billing|פיננס|חשבונאות|הנהלת חשבונות",
    ),
    "Sales & BD": (
        r"\bsales(?!force)|business development|(?-i:\bBD\b)|\bBDR\b|\bSDR\b|account (executive|manager|director)",
        r"partnership|partner manager|channel|revenue|presales|pre-sales|solutions? (consultant|engineer)|מכירות",
    ),
    "Customer success & support": (
        r"customer (success|support|service|care|experience)|\bCSM\b|client success|technical support",
        r"support (engineer|specialist|agent|analyst|representative)|onboarding|solutions? success|שירות לקוחות",
    ),
}

ROLE_TYPE_NAMES = (*ROLE_TYPES, OTHER)


def compile_role_types(table: dict[str, tuple[str, ...]]) -> dict[str, re.Pattern[str]]:
    """One case-insensitive pattern per role type, from a table shaped like `ROLE_TYPES`."""
    return {name: re.compile("|".join(parts), re.I) for name, parts in table.items()}


_COMPILED = compile_role_types(ROLE_TYPES)


def level(title: str) -> str:
    """The level a title states: Intern, Junior, or Not specified (Senior and the like are not recognised yet)."""
    if INTERN.search(title):
        return INTERN_LEVEL
    if JUNIOR.search(title):
        return JUNIOR_LEVEL
    return NOT_SPECIFIED


def role_types(title: str) -> list[str]:
    """Every role type the title fits, in `ROLE_TYPE_NAMES` order; [Other] when it fits none."""
    found = [name for name, pattern in _COMPILED.items() if pattern.search(title)]
    return found or [OTHER]


def matches(title: str, levels: Iterable[str] = (), types: Iterable[str] = ()) -> bool:
    """True when the job fits both the levels and the role types chosen. An empty choice means every one: Product with
    no level chosen (or every level) gives Product at any seniority, Product with Intern and Junior gives only the
    entry-level ones. A role type that does not exist matches nothing."""
    wanted_levels = set(levels)
    wanted_types = set(types)
    return (not wanted_levels or level(title) in wanted_levels) and (
        not wanted_types or not wanted_types.isdisjoint(role_types(title))
    )


def share_not_other(titles: Iterable[str]) -> float:
    """Share (0 to 1) of the titles placed in a role type other than Other; 1.0 for no titles."""
    counts = Counter(role_types(title) != [OTHER] for title in titles)
    total = counts[True] + counts[False]
    return counts[True] / total if total else 1.0


def report(titles: Iterable[str], sample: int = 15) -> str:
    """Role-type quality report for the daily log: the share not in Other, the count per type (a job with several
    types counts in each, so the counts can exceed the total) and the first distinct titles left in Other."""
    titles = list(titles)
    per_type: Counter[str] = Counter()
    others: dict[str, None] = {}
    for title in titles:
        types = role_types(title)
        per_type.update(types)
        if types == [OTHER]:
            others[title] = None
    lines = [f"Role types: {share_not_other(titles):.1%} of {len(titles)} open jobs are not in {OTHER}"]
    lines.extend(f"  {name}: {per_type[name]}" for name in ROLE_TYPE_NAMES)
    if others:
        lines.append(f"  Unplaced titles (first {min(sample, len(others))}):")
        lines.extend(f"    {title}" for title in list(others)[:sample])
    return "\n".join(lines)
