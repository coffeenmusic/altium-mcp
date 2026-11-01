"""Selecting from get_project_components' reply.

On a large project the whole list does not fit in a tool result: 1,000
components come to 200 KB, and 3.7 MB with their parameters. A client that
saves such a reply to a file leaves the model with nothing. So the list is
filtered and paged here, after Altium returns it: a call takes a few seconds,
and parameters vary too much per component for a fixed page size to be safe.
"""
import fnmatch
import json

# A reply stops about here and says where to go on: some 20,000 tokens
MAX_REPLY_CHARS = 60000


def _matches(text, pattern) -> bool:
    return fnmatch.fnmatchcase(str(text).upper(), str(pattern).upper())


def _named_parameters(component: dict, wanted: set) -> dict:
    kept = [p for p in component.get("parameters", []) if str(p.get("name", "")).upper() in wanted]
    return {**component, "parameters": kept}


def compact_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def select(data: dict, designators=None, sheet="", offset=0, limit=0, parameter_names=None,
           max_chars=MAX_REPLY_CHARS) -> dict:
    """The matching components from offset on, as many as limit (0: no limit)
    and max_chars allow, at least one. next_offset is None on the last page."""
    found = data.get("components", [])
    if designators:
        found = [c for c in found if any(_matches(c.get("designator", ""), d) for d in designators)]
    if sheet:
        found = [c for c in found if _matches(c.get("sheet", ""), sheet)]
    if parameter_names:
        wanted = {str(n).upper() for n in parameter_names}
        found = [_named_parameters(c, wanted) for c in found]

    offset = max(0, int(offset or 0))
    page, size = [], 0
    for component in found[offset:]:
        size += len(compact_json(component)) + 1
        if page and (size > max_chars or (limit and len(page) >= limit)):
            break
        page.append(component)
    end = offset + len(page)
    return {
        "project_path": data.get("project_path"),
        "component_count": data.get("component_count", len(data.get("components", []))),
        "matched": len(found),
        "offset": offset,
        "returned": len(page),
        "next_offset": end if end < len(found) else None,
        "components": page,
    }
