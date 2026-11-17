"""Family reference records cannot establish live infrastructure or a local incident."""
import re


def source_only_reason(question):
    q = question.lower()
    local = re.search(r"\b(my|our)\s+(sample|file|device|machine|laptop|computer|network|incident|host)\b|\bi found\b|\buploaded\b", q)
    live = re.search(r"\b(current|live|active|today)\b|right now", q)
    infrastructure = re.search(r"\b(c2|ip|address|addresses|domain|domains|campaign|operator)\b", q)
    if local:
        return "A family reference record contains no observations of the analyst's file, device or incident."
    if live and infrastructure:
        return "A dated family reference record does not establish live infrastructure or current campaign activity."
    return None
