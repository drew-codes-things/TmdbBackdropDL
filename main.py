import requests
import sys
import os
import re
import time
import json
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

import logging

_log_handlers = [logging.StreamHandler()]
_log_file = os.environ.get("LOG_FILE")
if _log_file:
    _log_handlers.append(logging.FileHandler(_log_file))
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(levelname)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("tmdb-backdrop")


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH   = os.path.join(SCRIPT_DIR, ".env")

try:
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)
except ImportError:
    pass

TMDB_BASE    = "https://api.themoviedb.org/3"
TMDB_IMAGE   = "https://image.tmdb.org/t/p"
SIZES        = ["original", "w1280", "w780", "w300"]
DEFAULT_SIZE = "original"
MAX_RESULTS  = 8

PREFERRED_BACKDROP_WIDTH  = 1920
PREFERRED_BACKDROP_HEIGHT = 1080

CACHE_FILE       = os.path.join(SCRIPT_DIR, ".tmdb_cache.json")
DOWNLOAD_WORKERS = 4
_cache           = None


def _load_cache():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
        _cache.setdefault("search", {})
        _cache.setdefault("images", {})
    return _cache


def _save_cache():
    if _cache is None:
        return
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f)
    except OSError:
        pass


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def estimate_total_size(urls: list) -> tuple:
    def head(u):
        try:
            r = requests.head(u, timeout=15, allow_redirects=True)
            return int(r.headers.get("content-length", 0))
        except requests.RequestException:
            return 0
    total = known = 0
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        for size in pool.map(head, urls):
            if size:
                total += size
                known += 1
    return total, known


def get_api_key() -> str:
    env_key = os.environ.get("TMDB_API_KEY", "").strip()
    if env_key:
        return env_key
    logger.error("No TMDB API key found.")
    print(f"       Create {ENV_PATH} with: TMDB_API_KEY=your_key")
    sys.exit(1)


def tmdb_get(url, params, retries=5):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 429:
                try:
                    wait = int(r.headers.get("Retry-After", 2 * (attempt + 1)))
                except (TypeError, ValueError):
                    wait = 2 * (attempt + 1)
                print(f"  [429] Rate limited -> waiting {wait}s before retry {attempt + 1}/{retries}...")
                time.sleep(wait)
                continue
            return r
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return None


def check_api_key(api_key: str) -> bool:
    try:
        r = tmdb_get(f"{TMDB_BASE}/configuration", {"api_key": api_key})
        return r is not None and r.status_code == 200
    except requests.RequestException:
        return False


def search_media(api_key: str, query: str, type_filter: str = "any") -> list:
    """
    Search TMDB for a title and return matching movie/TV results.

    Guards against tmdb_get returning None (all retries exhausted) to avoid
    an AttributeError on .raise_for_status() being called on None.
    """
    cache = _load_cache()
    cache_key = f"{type_filter}:{query.strip().lower()}"
    if cache_key in cache["search"]:
        return cache["search"][cache_key]
    try:
        r = tmdb_get(
            f"{TMDB_BASE}/search/multi",
            {"api_key": api_key, "query": query, "include_adult": False},
        )
        if r is None:
            print(f"  Search error: all retries exhausted for query '{query}'")
            return []
        r.raise_for_status()
        results = [x for x in r.json().get("results", []) if x.get("media_type") in ("movie", "tv")]
        if type_filter == "movie":
            results = [x for x in results if x.get("media_type") == "movie"]
        elif type_filter == "tv":
            results = [x for x in results if x.get("media_type") == "tv"]
        cache["search"][cache_key] = results
        _save_cache()
        return results
    except requests.RequestException as e:
        print(f"  Search error: {e}")
        return []


def get_images(api_key: str, media_id: int, media_type: str, lang_filter: str = "any") -> dict:
    """
    Fetch backdrop and poster images for a TMDB title.

    Guards against tmdb_get returning None (all retries exhausted) to avoid
    an AttributeError on .raise_for_status() being called on None.
    """
    cache = _load_cache()
    cache_key = f"{media_type}:{media_id}"
    if cache_key in cache["images"]:
        data = cache["images"][cache_key]
    else:
        try:
            r = tmdb_get(
                f"{TMDB_BASE}/{media_type}/{media_id}/images",
                {"api_key": api_key},
            )
            if r is None:
                print(f"  Error fetching images: all retries exhausted for id {media_id}")
                return {"backdrops": [], "posters": []}
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            print(f"  Error fetching images: {e}")
            return {"backdrops": [], "posters": []}
        cache["images"][cache_key] = {"backdrops": data.get("backdrops", []),
                                      "posters": data.get("posters", [])}
        _save_cache()

    backdrops = data.get("backdrops", [])
    posters   = data.get("posters",   [])

    if lang_filter == "en":
        backdrops = [b for b in backdrops if b.get("iso_639_1") == "en"]
        posters   = [p for p in posters   if p.get("iso_639_1") == "en"]
    elif lang_filter == "none":
        backdrops = [b for b in backdrops if b.get("iso_639_1") is None]
        posters   = [p for p in posters   if p.get("iso_639_1") is None]
    else:
        backdrops = [b for b in backdrops if b.get("iso_639_1") in ("en", None)]
        posters   = [p for p in posters   if p.get("iso_639_1") in ("en", None)]

    return {"backdrops": backdrops, "posters": posters}


def pick_best_backdrop(backdrops: list) -> list:
    if not backdrops:
        return []

    def sort_key(b):
        w = b.get("width", 0)
        h = b.get("height", 0)
        is_preferred = (w == PREFERRED_BACKDROP_WIDTH and h == PREFERRED_BACKDROP_HEIGHT)
        dist  = abs(w - PREFERRED_BACKDROP_WIDTH) + abs(h - PREFERRED_BACKDROP_HEIGHT)
        score = b.get("vote_average", 0)
        return (0 if is_preferred else 1, dist, -score)

    return sorted(backdrops, key=sort_key)


def sanitize(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\[\]()]', "", name).strip().replace(" ", "_")


def get_output_dir(base_dir: str, media_type: str, title: str, year: str,
                  organised: bool, genre_hint: str = "") -> str:
    safe     = sanitize(title)
    year_tag = f" ({year})" if year and year != "????" else ""
    if not organised:
        return os.path.join(base_dir, safe)

    genre_tokens = {tok for tok in re.split(r"[\s,|/]+", genre_hint.strip()) if tok}
    if ("anime" in genre_hint.lower()) or ("16" in genre_tokens):
        category = "Anime"
    elif media_type == "tv":
        category = "TV"
    else:
        category = "Movies"

    folder_name = sanitize(f"{title}{year_tag}")
    return os.path.join(base_dir, category, folder_name)


def download_image(url: str, dest: str) -> bool:
    for attempt in range(5):
        try:
            r = requests.get(url, stream=True, timeout=30)
            if r.status_code == 429:
                try:
                    wait = int(r.headers.get("Retry-After", 2 * (attempt + 1)))
                except (TypeError, ValueError):
                    wait = 2 * (attempt + 1)
                if attempt < 4:
                    print(f"  [429] Rate limited image download -> waiting {wait}s...")
                    time.sleep(wait)
                    continue
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with open(dest, "wb") as f, tqdm(
                desc=os.path.basename(dest),
                total=total,
                unit="iB",
                unit_scale=True,
                unit_divisor=1024,
                leave=False,
            ) as bar:
                for chunk in r.iter_content(1024):
                    f.write(chunk)
                    bar.update(len(chunk))
            return True
        except requests.RequestException as e:
            if attempt == 4:
                print(f"  Download error: {e}")
                return False
            wait = 2 ** attempt
            print(f"  Transient download error ({attempt + 1}/5): {e} -> retrying in {wait}s...")
            time.sleep(wait)
        except IOError as e:
            print(f"  File write error: {e}")
            return False
    return False


def bulk_download(urls_and_paths: list) -> None:
    total_bytes, known = estimate_total_size([u for u, _ in urls_and_paths])
    if known:
        approx = "" if known == len(urls_and_paths) else f" (estimated from {known}/{len(urls_and_paths)})"
        print(f"  Downloading {len(urls_and_paths)} image(s) ~{human_size(total_bytes)}{approx}...")
    else:
        print(f"  Downloading {len(urls_and_paths)} image(s)...")
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        futures = {pool.submit(download_image, url, path): path for url, path in urls_and_paths}
        ok = fail = 0
        for f in as_completed(futures):
            if f.result():
                ok += 1
            else:
                fail += 1
    print(f"  Done -> {ok} saved, {fail} failed.")


def pick_int(prompt: str, lo: int, hi: int):
    while True:
        raw = input(prompt).strip()
        if raw.lower() in ("q", "quit", "exit"):
            return None
        if raw.isdigit() and lo <= int(raw) <= hi:
            return int(raw)
        print(f"  Please enter a number between {lo} and {hi}, or q to go back.")


def display_results(results: list) -> None:
    for i, r in enumerate(results, 1):
        mtype = "TV" if r["media_type"] == "tv" else "Movie"
        title = r.get("name") or r.get("title", "Unknown")
        year  = (r.get("first_air_date") or r.get("release_date") or "????")[:4]
        votes = r.get("vote_average", 0)
        print(f"  {i:>2}. [{mtype}] {title} ({year})  * {votes:.1f}")


def display_backdrops(backdrops: list, title: str) -> None:
    print(f"\n  {len(backdrops)} backdrop(s) for '{title}' (sorted: 1080p first, then closest):")
    for i, b in enumerate(backdrops, 1):
        lang = b.get("iso_639_1") or "neutral"
        w, h = b.get("width", "?"), b.get("height", "?")
        vt   = b.get("vote_average", 0)
        tag  = " [1080p]" if (w == 1920 and h == 1080) else ""
        print(f"  {i:>3}. {w}x{h}{tag}  lang={lang}  *{vt:.1f}")


def process_title(api_key, query, args, image_size, type_filter="any", index=None, total=None):
    """
    Search for a single title and:
      - in dry-run mode: print what would be matched without downloading.
      - otherwise: download all backdrops (and posters if requested).
    index/total are used for the [N/total] counter in batch mode.

    Batch auto-selection strategy: results are sorted by vote_count descending
    before picking index 0, so heavily-voted (high-confidence) entries are
    preferred over obscure partial-name matches that happen to appear first.
    """
    prefix = f"[{index}/{total}] " if (index is not None and total is not None) else ""

    results = search_media(api_key, query, type_filter)
    if not results:
        print(f"{prefix}[SKIP] No results for '{query}'")
        return

    results = results[:MAX_RESULTS]

    results_by_votes = sorted(results, key=lambda r: r.get("vote_count", 0), reverse=True)
    selected   = results_by_votes[0]
    media_id   = selected["id"]
    media_type = selected["media_type"]
    title      = selected.get("name") or selected.get("title", "Unknown")
    year       = (selected.get("first_air_date") or selected.get("release_date") or "????")[:4]
    genres_raw = " ".join(str(g) for g in selected.get("genre_ids", []))
    mtype_label = "TV" if media_type == "tv" else "Movie"

    if getattr(args, "dry_run", False):
        votes = selected.get("vote_average", 0)
        print(f"{prefix}'{query}'  ->  [{mtype_label}] {title} ({year})  * {votes:.1f}  (id={media_id})")
        if len(results_by_votes) > 1:
            runner_up = results_by_votes[1]
            ru_title  = runner_up.get("name") or runner_up.get("title", "?")
            ru_year   = (runner_up.get("first_air_date") or runner_up.get("release_date") or "????")[:4]
            ru_type   = "TV" if runner_up["media_type"] == "tv" else "Movie"
            print(f"         (runner-up: [{ru_type}] {ru_title} ({ru_year}))")
        return

    print(f"{prefix}{title} ({year}) [{media_type}]")

    images    = get_images(api_key, media_id, media_type)
    backdrops = pick_best_backdrop(images["backdrops"])
    posters   = images["posters"]

    out_dir = get_output_dir(args.output, media_type, title, year, args.organised, genres_raw)

    if not backdrops:
        print(f"  [SKIP] No backdrops found for '{title}'")
    else:
        tasks = [
            (
                f"{TMDB_IMAGE}/{image_size}{b['file_path']}",
                os.path.join(out_dir, f"backdrop_{i:03d}.jpg"),
            )
            for i, b in enumerate(backdrops, 1)
        ]
        bulk_download(tasks)

    if args.posters and posters:
        tasks = [
            (
                f"{TMDB_IMAGE}/{image_size}{p['file_path']}",
                os.path.join(out_dir, f"poster_{i:03d}.jpg"),
            )
            for i, p in enumerate(posters, 1)
        ]
        bulk_download(tasks)


def run_batch(api_key, args, image_size):
    if not os.path.isfile(args.batch):
        logger.error(f"Batch file not found: {args.batch}")
        sys.exit(1)
    titles = []
    with open(args.batch, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                titles.append(stripped)
    if not titles:
        print("Batch file is empty.")
        sys.exit(0)

    total = len(titles)
    if getattr(args, "dry_run", False):
        print(f"  Dry-run mode -> {total} title(s) -- no files will be downloaded\n")
    else:
        print(f"  Batch mode -> {total} title(s) to process\n")

    for i, title in enumerate(titles, 1):
        process_title(api_key, title, args, image_size, args.type, index=i, total=total)
        print()


def main():
    parser = argparse.ArgumentParser(description="TMDB Backdrop Downloader")
    parser.add_argument("--organised", action="store_true",
                        help="Organise output into Movies/TV/Anime subfolders")
    parser.add_argument("--posters", action="store_true",
                        help="Also download poster images alongside backdrops")
    parser.add_argument("--output", default="backdrops",
                        help="Base output directory (default: backdrops)")
    parser.add_argument("--batch", default=None,
                        help="Path to a .txt file with one title per line for batch downloading")
    parser.add_argument("--type", default="any", choices=["any", "movie", "tv"],
                        help="Filter search results by type: any (default), movie, or tv")
    parser.add_argument("--dry-run", action="store_true",
                        help="Batch only: print which title each line would match without downloading anything")
    parser.add_argument("--workers", type=int, default=4,
                        help="Concurrent download/size-estimate threads (default: 4)")
    args = parser.parse_args()

    if args.dry_run and not args.batch:
        parser.error("--dry-run requires --batch")

    if args.workers < 1:
        parser.error("--workers must be at least 1")
    global DOWNLOAD_WORKERS
    DOWNLOAD_WORKERS = args.workers

    api_key = get_api_key()
    if not check_api_key(api_key):
        logger.error(f"API key rejected by TMDB. Check TMDB_API_KEY in {ENV_PATH}.")
        sys.exit(1)

    print("\n  TMDB Backdrop Downloader")
    if args.organised:
        print("  [Organised mode: Movies / TV / Anime subfolders]")
    if args.posters:
        print("  [Poster mode: posters will be downloaded alongside backdrops]")
    if args.type != "any":
        print(f"  [Type filter: {args.type} only]")
    if args.dry_run:
        print("  [Dry-run mode: no files will be downloaded]")
    print("  " + "-" * 40)

    if args.batch:
        print("  Image size options:")
        for i, s in enumerate(SIZES, 1):
            print(f"    {i}. {s}")
        if args.dry_run:
            image_size = DEFAULT_SIZE
        else:
            size_choice = pick_int("  Choose size (default 1 = original): ", 1, len(SIZES))
            image_size  = SIZES[(size_choice - 1) if size_choice else 0]
        print()
        run_batch(api_key, args, image_size)
        return

    print("  Type 'q' at any prompt to go back / quit.\n")

    print("  Image size options:")
    for i, s in enumerate(SIZES, 1):
        print(f"    {i}. {s}")
    size_choice = pick_int("  Choose size (default 1 = original): ", 1, len(SIZES))
    image_size  = SIZES[(size_choice - 1) if size_choice else 0]
    print()

    while True:
        query = input("  Search (movie or TV show): ").strip()
        if query.lower() in ("q", "quit", "exit"):
            break
        if not query:
            continue

        results = search_media(api_key, query, args.type)
        if not results:
            print(f"  No results for '{query}'.")
            continue

        results = results[:MAX_RESULTS]
        print()
        display_results(results)
        print()

        choice = pick_int(f"  Select title (1-{len(results)}): ", 1, len(results))
        if choice is None:
            continue

        selected   = results[choice - 1]
        media_id   = selected["id"]
        media_type = selected["media_type"]
        title      = selected.get("name") or selected.get("title", "Unknown")
        year       = (selected.get("first_air_date") or selected.get("release_date") or "????")[:4]
        genres_raw = " ".join(str(g) for g in selected.get("genre_ids", []))

        print("\n  Language filter:")
        print("    1. English + language-neutral (recommended)")
        print("    2. English only")
        print("    3. Language-neutral only")
        print("    4. All")
        lang_choice = pick_int("  Choose filter (default 1): ", 1, 4)
        lang_map    = {1: "any", 2: "en", 3: "none", 4: "all"}
        lang_filter = lang_map.get(lang_choice or 1, "any")

        images    = get_images(api_key, media_id, media_type, lang_filter)
        backdrops = pick_best_backdrop(images["backdrops"])
        posters   = images["posters"]

        out_dir = get_output_dir(
            args.output, media_type, title, year,
            args.organised, genres_raw
        )

        if not backdrops:
            print(f"  No backdrops found for '{title}' with that filter.")
        else:
            display_backdrops(backdrops, title)
            print()
            print("  Backdrop download options:")
            print(f"    a. Download all {len(backdrops)} backdrop(s)")
            print(f"    1-{len(backdrops)}. Download a specific one")
            raw = input("  Choice: ").strip().lower()

            if raw == "a":
                tasks = [
                    (
                        f"{TMDB_IMAGE}/{image_size}{b['file_path']}",
                        os.path.join(out_dir, f"backdrop_{i:03d}.jpg"),
                    )
                    for i, b in enumerate(backdrops, 1)
                ]
                bulk_download(tasks)
            elif raw.isdigit() and 1 <= int(raw) <= len(backdrops):
                idx  = int(raw)
                url  = f"{TMDB_IMAGE}/{image_size}{backdrops[idx - 1]['file_path']}"
                dest = os.path.join(out_dir, f"backdrop_{idx:03d}.jpg")
                if download_image(url, dest):
                    print(f"  Saved: {dest}")
            else:
                print("  Invalid choice, skipping backdrops.")

        if args.posters:
            if not posters:
                print(f"  No posters found for '{title}' with that filter.")
            else:
                print(f"\n  {len(posters)} poster(s) found.")
                print("  Poster download options:")
                print(f"    a. Download all {len(posters)} poster(s)")
                print(f"    1-{len(posters)}. Download a specific one")
                raw_p = input("  Poster choice: ").strip().lower()

                if raw_p == "a":
                    tasks = [
                        (
                            f"{TMDB_IMAGE}/{image_size}{p['file_path']}",
                            os.path.join(out_dir, f"poster_{i:03d}.jpg"),
                        )
                        for i, p in enumerate(posters, 1)
                    ]
                    bulk_download(tasks)
                elif raw_p.isdigit() and 1 <= int(raw_p) <= len(posters):
                    idx  = int(raw_p)
                    url  = f"{TMDB_IMAGE}/{image_size}{posters[idx - 1]['file_path']}"
                    dest = os.path.join(out_dir, f"poster_{idx:03d}.jpg")
                    if download_image(url, dest):
                        print(f"  Saved: {dest}")
                else:
                    print("  Invalid choice, skipping posters.")

        again = input("\n  Search for something else? y/n: ").strip().lower()
        if again != "y":
            break

    print("  Done.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
        sys.exit(0)
