from pathlib import Path
import sys

from gtts import gTTS


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: generate-vietnamese-voice.py TEXT_FILE OUTPUT_MP3")
    text = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit("Voice text is empty")
    gTTS(text=text, lang="vi", slow=False).save(sys.argv[2])


if __name__ == "__main__":
    main()
