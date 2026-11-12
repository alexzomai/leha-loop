#!/usr/bin/env python3
"""Atomic, locked updates to the legacy branch runtime cache (not the task registry)."""
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone


def atomic_json(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def main():
    path, command, *args = sys.argv[1:]
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not isinstance(data, dict): raise ValueError('invalid branch state; refusing to overwrite')
        if command == 'init':
            if path.exists(): return
        elif command == 'set':
            branch, *pairs = args
            if len(pairs) % 2: raise ValueError('expected key/value pairs')
            data.setdefault(branch, {}).update(dict(zip(pairs[::2], pairs[1::2])))
            data[branch]['updated'] = datetime.now(timezone.utc).isoformat()
        elif command == 'del': data.pop(args[0], None)
        else: raise ValueError('unknown state command')
        atomic_json(path, data)


if __name__ == '__main__': main()
