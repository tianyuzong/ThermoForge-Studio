"""Replay only unresolved user edits; assistant suggestions are context, not edits."""
import copy
import re


def user_prompts(prompt, conversation=None):
    context = conversation or {}
    pending = context.get('history', [])[context.get('applied_message_count', 0):]
    return [m['content'] for m in pending if m['role'] == 'user'] + [prompt]


def rule_candidate(engine, model_id, prompt, current, conversation=None):
    candidate = dict(config=copy.deepcopy(current or {}))
    unresolved = []
    prompts = user_prompts(prompt, conversation)
    for text in prompts:
        candidate = engine.make_plan(model_id, text, candidate['config'])
        # Validation errors are recalculated on the accumulated configuration.
        # Semantic questions cannot safely be cleared just because another
        # message changes a different field. Codex reviews them against history.
        unresolved.extend(q for q in candidate.get('questions', [])
                          if not q.startswith(('参数组合需要调整：', '草案缺少热源：')))
    if len(prompts) > 1 and unresolved:
        # Recompute the concrete structural scope question after an explicit
        # follow-up chooses a free body or supplies an actual support.
        st=candidate['config'].get('structural') or {}
        if st.get('supports') or re.search(r'自由(?:膨胀|热弹性|状态)|无约束','\n'.join(prompts)):
            unresolved=[q for q in unresolved if q!='请明确结构为自由状态，还是提供固定面和固定方向。']
        candidate['questions'] = list(dict.fromkeys(unresolved + candidate.get('questions', [])))
        candidate['ok'] = not candidate['questions']
    return candidate
