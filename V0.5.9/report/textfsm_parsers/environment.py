"""show environment power / cooling 출력 파싱."""
import re

_ENV_STATUS_RE = re.compile(r"\b(Ok|Failed|Failure|Not Inserted)\b", re.IGNORECASE)
_ENV_NOT_SUPPORTED_RE = re.compile(r"^\s*%|no power supplies|invalid input", re.IGNORECASE)


def parse_environment_status(output):
    """show environment power/cooling 출력 -> "Ok"/"Failed"/"N/A"(미지원)/"Unknown"."""
    if not output or not output.strip():
        return "N/A"
    text = output.strip()
    if _ENV_NOT_SUPPORTED_RE.match(text):
        return "N/A"
    statuses = _ENV_STATUS_RE.findall(text)
    if not statuses:
        return "Unknown"
    if any(s.lower() in ("failed", "failure") for s in statuses):
        return "Failed"
    return "Ok"
