from __future__ import annotations

import csv
import io
import json

from meeting.schemas import MeetingAnalyzeResponse


def export_json(
    meeting: MeetingAnalyzeResponse,
) -> bytes:
    payload = meeting.model_dump(
        mode="json"
    )

    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def export_csv(
    meeting: MeetingAnalyzeResponse,
) -> bytes:
    buffer = io.StringIO()

    writer = csv.writer(buffer)

    writer.writerow(
        [
            "section",
            "assignee",
            "task",
            "deadline",
            "priority",
            "text",
            "evidence_segment_ids",
        ]
    )

    for item in meeting.analysis.summary:
        writer.writerow(
            [
                "summary",
                "",
                "",
                "",
                "",
                item,
                "",
            ]
        )

    for topic in meeting.analysis.topics:
        writer.writerow(
            [
                "topic",
                "",
                "",
                "",
                "",
                f"{topic.title}: "
                + "; ".join(
                    topic.theses
                ),
                ",".join(
                    str(x)
                    for x
                    in topic.evidence_segment_ids
                ),
            ]
        )

    for decision in meeting.analysis.decisions:
        writer.writerow(
            [
                "decision",
                "",
                "",
                "",
                "",
                decision.text,
                ",".join(
                    str(x)
                    for x
                    in decision.evidence_segment_ids
                ),
            ]
        )

    for question in meeting.analysis.open_questions:
        writer.writerow(
            [
                "open_question",
                "",
                "",
                "",
                "",
                question.text,
                ",".join(
                    str(x)
                    for x
                    in question.evidence_segment_ids
                ),
            ]
        )

    for item in meeting.analysis.action_items:
        writer.writerow(
            [
                "action_item",
                item.assignee or "",
                item.task,
                item.deadline or "",
                item.priority,
                "",
                ",".join(
                    str(x)
                    for x
                    in item.evidence_segment_ids
                ),
            ]
        )

    for risk in meeting.analysis.risks:
        writer.writerow(
            [
                "risk",
                "",
                "",
                "",
                risk.severity,
                risk.text,
                ",".join(
                    str(x)
                    for x
                    in risk.evidence_segment_ids
                ),
            ]
        )

    # BOM позволяет нормально открыть кириллицу в Excel.
    return buffer.getvalue().encode(
        "utf-8-sig"
    )
