"""Cut the prompt into arguments dsh can take.

dsh's headless profile reads its task from argv alone — no stdin — and joins
the words with one space. The kernel caps a single argument at 128 KiB
(MAX_ARG_STRLEN), and a served prompt passes that: the claude seat died there
with "Argument list too long" on bloomery night 10 and moved to stdin. Here
each piece ends where a space already stands and that space is dropped, so
dsh's join puts it back and the task arrives byte for byte.

    python3 prompt_pieces.py <prompt file> <directory>

writes the pieces as `0000`, `0001`, ... in the directory, in order.
"""

import sys
from pathlib import Path

# Bytes per piece: under the kernel's 131,072 with room to spare. A space is
# one byte in UTF-8 and never part of a longer character, so a cut at one
# never splits a character.
PIECE = 100_000


def pieces(text, size=PIECE):
    out = []

    while len(text) > size:
        cut = text.rfind(b" ", 0, size + 1)

        if cut < 0:
            raise SystemExit(f"prompt_pieces: no space in {size} bytes to cut the prompt at")

        out.append(text[:cut])
        text = text[cut + 1 :]

    out.append(text)

    return out


if __name__ == "__main__":
    prompt, directory = sys.argv[1:]
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)

    for n, piece in enumerate(pieces(Path(prompt).read_bytes())):
        (target / f"{n:04}").write_bytes(piece)
