"""Drive a --dev Q2 over Wi-Fi for device tests: tools/q2remote.py for input, the shell and screenshots, and
macOS Vision (ocr.swift) to read the screen, so a test finds a row by its text rather than by where it is.

Environment: Q2_HOST, and Q2_KEY_FILE (default: the repository's q2remote.key). OCR: the ocr binary built from
ocr.swift, at $Q2_OCR or next to this file."""
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get('Q2_REPO', Path.home() / 'code/q2-pod'))
REMOTE = REPO / 'tools/q2remote.py'
OCR = Path(os.environ.get('Q2_OCR', HERE / 'ocr'))


@dataclass
class Text:
    x: int
    y: int
    w: int
    h: int
    text: str

    @property
    def centre(self):
        return self.x + self.w // 2, self.y + self.h // 2


@dataclass
class Screen:
    path: Path
    texts: list

    def find(self, text, exact=False, below=0):
        """The first run whose text is text (exact) or holds it, ignoring case, at or below y=below."""
        want = text.lower()
        for t in sorted(self.texts, key=lambda t: (t.y, t.x)):
            have = t.text.lower().strip()
            if t.y >= below and (have == want if exact else want in have):
                return t
        return None

    def has(self, text, exact=False):
        return self.find(text, exact) is not None

    def words(self):
        return ' | '.join(t.text for t in sorted(self.texts, key=lambda t: (t.y, t.x)))


class Q2:
    def __init__(self, out):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.n = 0
        self.env = dict(os.environ)
        self.env.setdefault('Q2_KEY_FILE', str(REPO / 'q2remote.key'))

    def remote(self, *args, timeout=90):
        r = subprocess.run(['python3', str(REMOTE), *map(str, args)], env=self.env, capture_output=True,
                           text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr

    def sh(self, command, timeout=90, quiet_s=30):
        """(status, output) of command run as root on the Q2"""
        return self.remote('sh', '--timeout', quiet_s, command, timeout=timeout)

    def out_of(self, command, **kw):
        return self.sh(command, **kw)[1].strip()

    def state(self):
        _, text = self.remote('state')
        return dict(re.findall(r'(\w+)=(\S+)', text))

    # Input on a dark screen does other things (the wheel sets the volume), so each one lights it first:
    # q2remote's wake does nothing while it is lit.
    def key(self, name, ms=None):
        self.wake()
        self.remote('key', name, *([ms] if ms else []))

    def wheel(self, n):
        self.wake()
        self.remote('wheel', n)

    def tap(self, x, y):
        self.wake()
        self.remote('tap', x, y)

    def swipe(self, x1, y1, x2, y2, ms=300):
        self.wake()
        self.remote('swipe', x1, y1, x2, y2, ms)

    def shot(self, label='shot', settle=True):
        self.n += 1
        name = re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')
        path = self.out / f'{self.n:03d}-{name}.png'
        self.remote('shot', *(['--settle'] if settle else []), path)
        texts = []
        if path.exists():
            # OCR reads small text better at twice the size; boxes are halved back to the screen's pixels
            big = path.with_suffix('.x2.png')
            subprocess.run(['sips', '-z', '640', '750', str(path), '--out', str(big)], capture_output=True)
            r = subprocess.run([str(OCR), str(big)], capture_output=True, text=True, timeout=30)
            big.unlink(missing_ok=True)
            for line in r.stdout.splitlines():
                box, _, text = line.partition('\t')
                x, y, w, h = (int(v) // 2 for v in box.split())
                texts.append(Text(x, y, w, h, text))
        return Screen(path, texts)

    def wake(self):
        self.remote('wake')

    def tap_text(self, text, exact=False, label=None, scroll=12, below=30, step=3):
        """Taps the row showing text, turning the wheel down (then up) to find it. Returns the screen it tapped
        on, or None when it never showed."""
        for direction in (step, -step):
            for _ in range(scroll):
                s = self.shot(label or f'find {text}')
                t = s.find(text, exact, below)
                if t:
                    self.tap(*t.centre)
                    time.sleep(0.8)
                    return s
                self.wheel(direction)
                time.sleep(0.4)
        return None

    def home(self, tries=8):
        """Return until Home's title (the theme's "Q2 Pod") shows. Returns that screen, or None."""
        for _ in range(tries):
            s = self.shot('home?')
            title = s.find('Q2 Pod')
            if title and title.y < 30:
                return s
            self.key('return')
            time.sleep(0.6)
        return None
