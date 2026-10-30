"""Download and verify BGE model files for the Docker /models bind mount."""

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import ProxyHandler, Request, build_opener


BASE_URL = "https://modelscope.cn/api/v1/models"
REPOSITORIES = ("BAAI/bge-m3", "BAAI/bge-reranker-v2-m3")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_ranges(url, partial, size, label):
    part_size = 64 * 1024 * 1024
    directory = partial.with_name(partial.name + ".chunks")
    directory.mkdir(exist_ok=True)
    count = (size + part_size - 1) // part_size

    def fetch(index):
        start = index * part_size
        end = min(size, start + part_size) - 1
        chunk = directory / str(index)
        if chunk.exists() and chunk.stat().st_size == end - start + 1:
            return chunk
        for attempt in range(5):
            try:
                opener = build_opener(ProxyHandler({}))
                request = Request(url, headers={"Range": f"bytes={start}-{end}"})
                with opener.open(request, timeout=60) as response:
                    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{size}":
                        raise RuntimeError("Server did not return requested byte range")
                    with chunk.open("wb") as stream:
                        for block in iter(lambda: response.read(1024 * 1024), b""):
                            stream.write(block)
                if chunk.stat().st_size != end - start + 1:
                    raise RuntimeError("Incomplete download segment")
                print(f"{label}: segment {index + 1}/{count} ready", flush=True)
                return chunk
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2)

    with ThreadPoolExecutor(max_workers=8) as pool:
        pieces = list(pool.map(fetch, range(count)))
    with partial.open("wb") as stream:
        for piece in pieces:
            with piece.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    stream.write(block)
    return pieces, directory


def download_model(repo, output_dir):
    opener = build_opener(ProxyHandler({}))
    with opener.open(f"{BASE_URL}/{repo}/repo/files?Revision=master&Recursive=true", timeout=60) as response:
        listing = json.load(response)
    if not listing.get("Success"):
        raise RuntimeError(f"Cannot list {repo}: {listing.get('Message')}")
    files = [item for item in listing["Data"]["Files"] if item["Type"] == "blob"]
    names = {item["Path"] for item in files}
    weight = "model.safetensors" if "model.safetensors" in names else "pytorch_model.bin"
    if weight not in names:
        raise RuntimeError(f"Missing weights in {repo}")
    selected = [item for item in files if item["Path"] == weight or (
        "/" not in item["Path"] and item["Path"].endswith((".json", ".txt", ".model", ".pt"))
    ) or item["Path"] == "1_Pooling/config.json"]
    model_dir = output_dir / repo.split("/")[-1]
    model_dir.mkdir(parents=True, exist_ok=True)
    print(f"{repo}: {len(selected)} files, {sum(item['Size'] for item in selected) / 1024**3:.2f} GiB", flush=True)
    for item in selected:
        destination = (model_dir / item["Path"]).resolve()
        if not destination.is_relative_to(model_dir.resolve()):
            raise RuntimeError("Unsafe model file path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        expected_hash = item["Sha256"]
        if len(expected_hash) != 64:
            raise RuntimeError(f"Missing SHA-256 for {repo}/{item['Path']}")
        if destination.exists() and destination.stat().st_size == item["Size"] and sha256(destination) == expected_hash:
            print(f"Verified existing: {repo}/{item['Path']}", flush=True)
            continue
        partial = destination.with_name(destination.name + ".part")
        url = f"{BASE_URL}/{repo}/repo?" + urlencode({
            "Revision": item["Revision"], "FilePath": item["Path"]
        }, quote_via=quote)
        for attempt in range(5):
            try:
                if item["Size"] > 128 * 1024 * 1024:
                    pieces, directory = download_ranges(url, partial, item["Size"], f"{repo}/{item['Path']}")
                    if sha256(partial) != expected_hash:
                        for piece in pieces:
                            piece.unlink()
                        raise RuntimeError("Segmented download SHA-256 mismatch")
                    partial.replace(destination)
                    for piece in pieces:
                        piece.unlink()
                    directory.rmdir()
                    print(f"Verified: {repo}/{item['Path']}", flush=True)
                    break
                offset = partial.stat().st_size if partial.exists() else 0
                headers = {"Range": f"bytes={offset}-"} if offset else {}
                with opener.open(Request(url, headers=headers), timeout=60) as response:
                    if offset and response.status != 206:
                        offset = 0
                    if offset and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                        raise RuntimeError("Invalid resume range")
                    last_update = time.monotonic()
                    with partial.open("ab" if offset else "wb") as stream:
                        for block in iter(lambda: response.read(1024 * 1024), b""):
                            stream.write(block)
                            offset += len(block)
                            if time.monotonic() - last_update >= 10:
                                print(f"{repo}/{item['Path']}: {offset / 1024**2:.0f}/{item['Size'] / 1024**2:.0f} MiB", flush=True)
                                last_update = time.monotonic()
                if partial.stat().st_size != item["Size"] or sha256(partial) != expected_hash:
                    partial.unlink()
                    raise RuntimeError("Downloaded file size or SHA-256 mismatch")
                partial.replace(destination)
                print(f"Verified: {repo}/{item['Path']}", flush=True)
                break
            except Exception as error:
                if attempt == 4:
                    raise
                print(f"Retry {attempt + 1}: {repo}/{item['Path']}: {error}", flush=True)
                time.sleep(2)
    manifest = {"repository": repo, "source": BASE_URL, "files": selected}
    (model_dir / "download-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return str(model_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "models")
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=2) as pool:
        for path in pool.map(lambda repo: download_model(repo, args.output_dir.resolve()), REPOSITORIES):
            print(f"READY: {path}", flush=True)
