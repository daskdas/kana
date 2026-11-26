"""Generate Basic Hiragana audio once and embed it in a standalone Hibi HTML.

Requires Python 3, websockets >= 14, ffmpeg, and OPENAI_API_KEY.
Usage: python generate-hibi-kana-audio.py [--sample]
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

MODEL = "gpt-realtime-2.1-mini"
VOICE = "marin"
KANA = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
SAMPLE = "あかきくけこ"
AUDIO_PLACEHOLDER = '<script id="basic-kana-audio" type="application/json">{}</script>'


def receive(socket):
    event = json.loads(socket.recv(timeout=60))
    if event["type"] == "error":
        raise RuntimeError(event["error"]["message"])
    return event


def generate_pcm(socket, kana):
    instruction = (
        f"日本語の発音教材です。「{kana}」の音だけを、標準的な日本語で"
        "自然に一度発音してください。説明、挨拶、引用の読み上げや余分な音は加えないでください。"
    )
    if kana == "を":
        instruction += "現代標準日本語の助詞の発音（おと同じ音）で。"
    if kana == "ん":
        instruction += "母音を足さず、鼻音だけで。"
    socket.send(json.dumps({
        "type": "response.create",
        "response": {
            "conversation": "none",
            "output_modalities": ["audio"],
            "instructions": instruction,
        },
    }))
    pcm = bytearray()
    while True:
        event = receive(socket)
        if event["type"] == "response.output_audio.delta":
            pcm.extend(base64.b64decode(event["delta"]))
        if event["type"] == "response.done":
            if event["response"]["status"] != "completed" or not pcm:
                raise RuntimeError(f"No completed audio for {kana}")
            return bytes(pcm)


def encode_mp3(pcm):
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "pipe:0",
        "-af", "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
        "silenceremove=start_periods=1:start_threshold=-45dB,areverse,apad=pad_dur=0.06",
        "-codec:a", "libmp3lame", "-b:a", "48k", "-f", "mp3", "pipe:1",
    ], input=pcm, capture_output=True, check=True)
    if len(result.stdout) < 500:
        raise RuntimeError("Generated clip is empty after trimming")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true", help="Generate six clips for a listening check")
    args = parser.parse_args()
    source = Path(__file__).with_name("hibi.html")
    html = source.read_text(encoding="utf-8")
    if html.count(AUDIO_PLACEHOLDER) != 1:
        parser.error("Expected one empty Basic Kana audio slot in hibi.html.")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        parser.error("Set OPENAI_API_KEY in your local environment to generate audio.")
    if not shutil.which("ffmpeg"):
        parser.error("Install ffmpeg before generating audio.")
    try:
        from websockets.sync.client import connect
    except ImportError:
        parser.error("Install websockets >= 14 before generating audio.")

    clip_dir = source.with_name("hibi-basic-kana-clips")
    clip_dir.mkdir(exist_ok=True)
    clips = {}
    characters = SAMPLE if args.sample else KANA
    missing = [kana for kana in characters if not (clip_dir / f"{ord(kana):04x}.mp3").is_file()]
    if missing:
        with connect(
            f"wss://api.openai.com/v1/realtime?model={MODEL}",
            additional_headers={"Authorization": f"Bearer {api_key}"},
        ) as socket:
            socket.send(json.dumps({
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "audio": {"output": {"voice": VOICE, "format": {"type": "audio/pcm", "rate": 24000}}},
                },
            }))
            while receive(socket)["type"] != "session.updated":
                pass
            for kana in missing:
                clip_path = clip_dir / f"{ord(kana):04x}.mp3"
                audio = encode_mp3(generate_pcm(socket, kana))
                clip_path.write_bytes(audio)
                print(f"Saved {kana} → {clip_path.name}", flush=True)
    for kana in characters:
        audio = (clip_dir / f"{ord(kana):04x}.mp3").read_bytes()
        clips[kana] = "data:audio/mpeg;base64," + base64.b64encode(audio).decode("ascii")
    embedded = AUDIO_PLACEHOLDER.replace("{}", json.dumps(clips, ensure_ascii=False))
    output = source.with_name("hibi-kana-audio-sample.html" if args.sample else "hibi-basic-kana-audio.html")
    output.write_text(html.replace(AUDIO_PLACEHOLDER, embedded, 1), encoding="utf-8")
    print(f"Wrote {output} with {len(clips)} clips. Listen through the clips before sharing.")


if __name__ == "__main__":
    main()
