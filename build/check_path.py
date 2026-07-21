import os
import sys

cwd = os.getcwd()
bad = sorted({c for c in cwd if ord(c) >= 128})
if bad:
    print("Path check: NON-ASCII characters found in current path:")
    print("  " + cwd)
    print("  Offending characters: " + repr("".join(bad)))
    sys.exit(1)
print("Path check: OK (pure ASCII path): " + cwd)
sys.exit(0)
