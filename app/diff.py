from typing import Any, List, Tuple


def diff_values(old: Any, new: Any, path: str = "root") -> List[Tuple[str, str, Any, Any]]:
    changes: List[Tuple[str, str, Any, Any]] = []

    if type(old) != type(new):
        changes.append((path, "type_changed", old, new))
        return changes

    if isinstance(old, dict):
        keys = set(old.keys()) | set(new.keys())
        for key in sorted(keys):
            new_path = f"{path}.{key}" if path != "root" else key
            if key not in old:
                changes.append((new_path, "added", None, new[key]))
            elif key not in new:
                changes.append((new_path, "removed", old[key], None))
            else:
                changes.extend(diff_values(old[key], new[key], new_path))
        return changes

    if isinstance(old, list):
        if len(old) != len(new):
            changes.append((path, "list_length_changed", old, new))
            return changes

        for i, (ov, nv) in enumerate(zip(old, new)):
            changes.extend(diff_values(ov, nv, f"{path}[{i}]"))
        return changes

    if old != new:
        changes.append((path, "value_changed", old, new))

    return changes
