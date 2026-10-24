#!/usr/bin/env python3
import json, sys

def main():
    if len(sys.argv) != 3:
        return 2
    with open(sys.argv[1], encoding='utf-8') as handle:
        json.load(handle)
    raise SystemExit('implement the certificate generator')

if __name__ == '__main__':
    raise SystemExit(main())
