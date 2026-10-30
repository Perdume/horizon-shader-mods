"""native/pack-options/logic.rs and native/pack-connect/options_logic.rs must be the same file.

    python -I tools/check_options_logic.py
"""
import hashlib
import sys

A = "native/pack-options/logic.rs"
B = "native/pack-connect/options_logic.rs"


def digest(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


if __name__ == "__main__":
    a, b = digest(A), digest(B)
    print(("same" if a == b else "DIFFERENT") + f": {A} {a[:12]} | {B} {b[:12]}")
    sys.exit(0 if a == b else 1)
