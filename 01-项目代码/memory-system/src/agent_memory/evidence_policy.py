"""Conservative speech-act guard; questions are context, not affirmative evidence."""

import re


def explicit_persistence_request(text: str) -> bool:
    if question_only(text) or re.search(r"(?:不要|无需|不用|不必).{0,20}(?:记住|保存|长期)", text):
        return False
    return bool(
        re.search(
            r"请.{0,80}(?:记住|保存|长期记忆)|(?:存入|保存到|加入)(?:长期)?记忆|(?:作为|设为)长期规则|跨会话记住|长期保存",
            text,
        )
    )


def question_only(text: str) -> bool:
    clauses = [part.strip() for part in re.findall(r"[^。！？!?；;\n]+[。！？!?；;]?", text) if part.strip()]
    if not clauses:
        return False

    def question(clause):
        value = clause.rstrip("。；;！! ")
        return bool(
            value.endswith(("?", "？", "吗", "么", "呢"))
            or re.search(r"是不是|是否|能否|有没有|为什么|怎么办|谁是|什么是", value)
            or re.match(
                r"(?i)^(?:is|are|was|were|does|do|did|can|could|who|what|why|where|when|how)\b", value
            )
        )

    return all(question(clause) for clause in clauses)
