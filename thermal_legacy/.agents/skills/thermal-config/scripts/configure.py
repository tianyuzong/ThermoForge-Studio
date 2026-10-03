"""Prepare or validate a Thermal Studio draft; never starts a simulation."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'validate'])
    parser.add_argument('--root', default=os.environ.get('THERMAL_STUDIO_ROOT', str(Path.cwd())))
    parser.add_argument('--input', required=True, help='UTF-8 JSON with model_id, prompt and current config')
    parser.add_argument('--review', help='Skill JSON containing patch and questions')
    parser.add_argument('--output', help='Optional path for the public context or validated draft')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not (root / 'agent.py').is_file() or not (root / 'server.py').is_file():
        parser.error('--root must point to the complete Thermal Studio application')
    sys.path.insert(0, str(root))
    import agent
    from agent_skill import prepare, compile_review
    from schemas import AgentRequest
    request = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
    validated_request = AgentRequest.model_validate(request)
    prepared = prepare(agent, validated_request.model_id, validated_request.prompt, validated_request.config,
                       validated_request.conversation())
    if args.action == 'prepare':
        result = prepared['public']
    else:
        if not args.review:
            parser.error('validate requires --review')
        config, questions = compile_review(agent, prepared, Path(args.review).read_text(encoding='utf-8-sig'))
        result = dict(ok=config is not None, config=config or prepared['original'], questions=questions,
                      changes=agent._codex_changes(config, prepared['original']) if config else [])
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        path = Path(args.output).resolve()
        if path in {Path(args.input).resolve(), Path(args.review).resolve() if args.review else None}:
            parser.error('--output must not overwrite the request or review')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + '\n', encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    print(output)
    if args.action == 'validate' and not result['ok']:
        return 2
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as error:
        sys.stderr.reconfigure(encoding='utf-8')
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
