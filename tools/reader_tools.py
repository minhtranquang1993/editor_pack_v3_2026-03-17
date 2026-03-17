#!/usr/bin/env python3
"""
Reader Tools wrapper for vakra-dev/reader

Usage:
  python3 tools/reader_tools.py scrape --url https://example.com
  python3 tools/reader_tools.py crawl --url https://example.com --depth 2 --max-pages 10
  python3 tools/reader_tools.py start --pool-size 3
  python3 tools/reader_tools.py status
  python3 tools/reader_tools.py stop
"""

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
OUT_DIR = WORKSPACE / "memory/reader"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def ts() -> str:
    return datetime.utcnow().strftime('%Y%m%d_%H%M%S')


def cmd_scrape(args):
    out_file = Path(args.output) if args.output else OUT_DIR / f"scrape_{ts()}.json"
    formats = args.formats or 'markdown'

    cmd = [
        'reader', 'scrape', args.url,
        '-f', formats,
        '-o', str(out_file),
        '-t', str(args.timeout_ms),
        '--batch-timeout', str(args.batch_timeout_ms),
    ]

    if args.standalone:
        cmd.append('--standalone')

    cp = run(cmd)
    if cp.returncode != 0:
        print('❌ reader scrape failed')
        print(cp.stderr[:1000] or cp.stdout[:1000])
        raise SystemExit(cp.returncode)

    data = json.loads(out_file.read_text(encoding='utf-8'))
    first = (data.get('data') or [{}])[0]
    md = first.get('markdown', '')

    print('✅ scrape ok')
    print(f'output_json: {out_file}')
    print(f'markdown_chars: {len(md)}')
    if args.print_markdown and md:
        print('---MARKDOWN---')
        print(md[:args.max_chars])


def cmd_crawl(args):
    out_file = Path(args.output) if args.output else OUT_DIR / f"crawl_{ts()}.json"
    formats = args.formats or 'markdown'

    cmd = [
        'reader', 'crawl', args.url,
        '-d', str(args.depth),
        '-m', str(args.max_pages),
        '-o', str(out_file),
        '--delay', str(args.delay_ms),
    ]

    if args.scrape:
        cmd += ['-s', '-f', formats]
    if args.timeout_ms:
        cmd += ['-t', str(args.timeout_ms)]
    if args.standalone:
        cmd.append('--standalone')

    cp = run(cmd)
    if cp.returncode != 0:
        print('❌ reader crawl failed')
        print(cp.stderr[:1000] or cp.stdout[:1000])
        raise SystemExit(cp.returncode)

    data = json.loads(out_file.read_text(encoding='utf-8'))
    urls = data.get('urls', [])
    print('✅ crawl ok')
    print(f'output_json: {out_file}')
    print(f'urls_found: {len(urls)}')


def cmd_start(args):
    cp = run(['reader', 'start', '--pool-size', str(args.pool_size)])
    if cp.returncode != 0:
        print(cp.stderr[:1000] or cp.stdout[:1000])
        raise SystemExit(cp.returncode)
    print(cp.stdout.strip() or '✅ daemon started')


def cmd_status(_args):
    cp = run(['reader', 'status'])
    print(cp.stdout.strip() or cp.stderr.strip())


def cmd_stop(_args):
    cp = run(['reader', 'stop'])
    print(cp.stdout.strip() or cp.stderr.strip())


def main():
    p = argparse.ArgumentParser(description='Reader tools wrapper')
    sp = p.add_subparsers(dest='cmd', required=True)

    p_s = sp.add_parser('scrape')
    p_s.add_argument('--url', required=True)
    p_s.add_argument('--formats', default='markdown')
    p_s.add_argument('--output')
    p_s.add_argument('--timeout-ms', type=int, default=30000)
    p_s.add_argument('--batch-timeout-ms', type=int, default=300000)
    p_s.add_argument('--standalone', action='store_true')
    p_s.add_argument('--print-markdown', action='store_true')
    p_s.add_argument('--max-chars', type=int, default=4000)
    p_s.set_defaults(func=cmd_scrape)

    p_c = sp.add_parser('crawl')
    p_c.add_argument('--url', required=True)
    p_c.add_argument('--depth', type=int, default=2)
    p_c.add_argument('--max-pages', type=int, default=20)
    p_c.add_argument('--scrape', action='store_true')
    p_c.add_argument('--formats', default='markdown')
    p_c.add_argument('--delay-ms', type=int, default=1000)
    p_c.add_argument('--timeout-ms', type=int)
    p_c.add_argument('--output')
    p_c.add_argument('--standalone', action='store_true')
    p_c.set_defaults(func=cmd_crawl)

    p_st = sp.add_parser('start')
    p_st.add_argument('--pool-size', type=int, default=3)
    p_st.set_defaults(func=cmd_start)

    p_stat = sp.add_parser('status')
    p_stat.set_defaults(func=cmd_status)

    p_sp = sp.add_parser('stop')
    p_sp.set_defaults(func=cmd_stop)

    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
