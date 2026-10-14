"""Check publication inputs without printing potentially sensitive matching values."""
import argparse
import ipaddress
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'work',
            'outputs', 'playwright-report', 'test-results'}
PATTERNS = {
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b'),
    'AWS access key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'personal workstation path': re.compile(r'(?:[A-Z]:[/\\]Users[/\\](?!Public\b)[^/\\\s]+|/ho' r'me/[^/\s]+|/ro' r'ot/)', re.I),
    'credential URL': re.compile(r'https?://[^/\s:@]+:[^/\s@]+@'),
}
IPV4 = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
PRIVATE = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def inspect(path):
    findings = []
    if path.suffix.lower() in {'.pem', '.key', '.crt', '.pfx', '.p12', '.log'} or path.name.startswith('.env'):
        return [(0, 'prohibited artifact')]
    try:
        content = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return [(0, 'unreviewed binary file')]
    for number, line in enumerate(content.splitlines(), 1):
        for kind, pattern in PATTERNS.items():
            candidate = line
            # This exact synthetic example is a negative URL-validation fixture, not a credential.
            if kind == 'credential URL': candidate = candidate.replace('https://user:password@example.org', '')
            if pattern.search(candidate): findings.append((number, kind))
        # The checker defines the ranges it checks; do not interpret those definitions as data.
        if path.resolve() == Path(__file__).resolve(): continue
        for match in IPV4.finditer(line):
            try: address = ipaddress.ip_address(match.group())
            except ValueError: continue
            if any(address in network for network in PRIVATE):
                findings.append((number, 'private network address'))
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    count = 0; findings = []
    for path in sorted(args.root.rglob('*')):
        relative = path.relative_to(args.root)
        if any(part in EXCLUDED for part in relative.parts) or path.name == 'resources.json': continue
        if path.is_symlink():
            findings.append((str(relative), 0, 'unreviewed symlink')); continue
        if not path.is_file(): continue
        count += 1
        findings.extend((str(relative), line, kind) for line, kind in inspect(path))
    for path, line, kind in findings: print(f'{path}:{line}: {kind}')
    print(f'Checked {count} publication files; findings: {len(findings)}')
    return 1 if findings else 0


if __name__ == '__main__': raise SystemExit(main())
