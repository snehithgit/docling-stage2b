"""Release roadmap; implemented means shipped code, not manual coverage."""
TITLES = (
    "Foundation and readiness", "Evidence eligibility and source selection",
    "Detection and contextual relationships", "Structured text and table parsing",
    "Diagram and image extraction", "Structured indexing and applicability",
    "Retrieval and evidence recovery", "Answer verification and evaluation",
    "Unified interface and worker controls", "Migration and release validation",
)

def phases():
    return [{"phase": i + 1, "version": f"5.0.{i}", "title": title,
             "status": "implemented" if i <= 8 else "in_progress"} for i, title in enumerate(TITLES)]
