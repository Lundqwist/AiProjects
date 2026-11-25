"""The coding team's models, always on this PC (online AI is never used for client code).

Each team role runs through wb_ai's local path (identity note, context sizing, AI activity trace) with its own
model: the Builder is the code expert (code_model), the Scout and Checker have models of their own in settings.
"""
from __future__ import annotations

import dataclasses
import json
import re

import wb_ai
from wb_settings import Settings

ROLES = {  # role: (wb_ai expert key whose model is replaced, settings field with the role's model)
    "builder": ("code", "code_model"),
    "scout": ("reasoning", "team_scout_model"),       # thinks first when its model can (qwen3, deepseek-r1)
    "checker": ("reasoning", "team_checker_model"),   # the reasoning expert thinks before it answers
}


def model_for(s: Settings, role: str) -> str:
    return getattr(s, ROLES[role][1]) or s.code_model


def ask(s: Settings, role: str, system: str, user: str, *, max_tokens: int = 8000,
        temperature: float = 0.2, think: bool = True) -> wb_ai.Answer:
    """think=False answers at once even with a thinking model: a qwen3 that thinks past its token budget
    returns no answer at all (Day 4: twice in three cart runs)."""
    key, field = ROLES[role]
    if not think:
        key = "code"   # wb_ai sends think=False for every expert but the reasoning one
    expert_setting = wb_ai.EXPERTS[key].setting
    local = dataclasses.replace(s, online_mode="off", **{expert_setting: model_for(s, role)})
    return wb_ai.ask(local, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                     task="code", expert=key, big=False, temperature=temperature, max_tokens=max_tokens)


def first_json(text: str) -> dict | None:
    """The first JSON object in a reply (models wrap it in ```json or add a sentence)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [m.group(1)] if m else []
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break
        start = text.find("{", start + 1) if not candidates else -1
    for c in candidates:
        try:
            v = json.loads(c)
            if isinstance(v, dict):
                return v
        except ValueError:
            continue
    return None
