<div align="center">

# TMDB-BackdropDownloader

**A Python CLI tool for downloading backdrop and poster images from TMDB, with batch mode, organised output, and dry-run support.**

[![Python](https://img.shields.io/badge/python-3.8+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![TMDB](https://img.shields.io/badge/TMDB-API-01B4E4?style=flat-square&logo=themoviedatabase&logoColor=white)](https://www.themoviedb.org/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

---

## Requirements

```
pip install -r requirements.txt
```

Dependencies: `requests`, `tqdm`, `python-dotenv` (optional)

Python 3.8+

---

## API Key Setup

A TMDB API key is required. Create a `.env` file in the project root:

```
TMDB_API_KEY=your_api_key_here
```

Get a free API key at [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api).

---

## Usage

### Interactive mode

```bash
python main.py
```

Search for a movie or TV show by name, pick from the results, choose a language filter and image size, then download individual or all backdrops. Use `q` at any prompt to go back or quit.

### Batch mode

```bash
python main.py --batch titles.txt
```

Place one title per line in a `.txt` file (lines starting with `#` are ignored). The tool searches TMDB for each title, auto-selects the highest-voted result, and downloads all backdrops.

### Dry run (batch only)

```bash
python main.py --batch titles.txt --dry-run
```

Prints which TMDB title each line would match without downloading any files.

---

## CLI Options

| Flag | Description |
|---|---|
| `--batch <file>` | Path to a `.txt` file with one title per line |
| `--output <dir>` | Base output directory (default: `backdrops`) |
| `--organised` | Sort output into `Movies/`, `TV/`, and `Anime/` subfolders |
| `--posters` | Download poster images alongside backdrops |
| `--type <any/movie/tv>` | Filter search results by media type (default: `any`) |
| `--dry-run` | Batch only: preview matches without downloading |
| `--workers <n>` | Concurrent download/size-estimate threads (default: `4`) |

---

## Image Sizes

Choose from the following sizes at runtime:

| Option | Size |
|---|---|
| 1 | original |
| 2 | w1280 |
| 3 | w780 |
| 4 | w300 |

Backdrops are sorted with 1920x1080 images first, then by closest resolution to 1080p, then by vote score.

---

## Output Structure

### Standard mode
```
backdrops/
  Movie_Title/
    backdrop_001.jpg
    backdrop_002.jpg
    poster_001.jpg
```

### Organised mode (`--organised`)
```
backdrops/
  Movies/
    Movie_Title_(2023)/
      backdrop_001.jpg
  TV/
    Show_Title_(2021)/
      backdrop_001.jpg
  Anime/
    Anime_Title_(2020)/
      backdrop_001.jpg
```

---

## Language Filters (Interactive Mode)

| Option | Filter |
|---|---|
| 1 | English + language-neutral (recommended) |
| 2 | English only |
| 3 | Language-neutral only |
| 4 | All languages |

---

## Notes

- Downloads run concurrently via `ThreadPoolExecutor` (`--workers`, default 4)
- A total-size estimate (via HEAD requests) is printed before each batch of downloads
- TMDB search and image lookups are cached to `.tmdb_cache.json` and reused across runs
- Rate-limited requests (HTTP 429) are automatically retried with the `Retry-After` header delay
- Failed downloads are retried up to 5 times with exponential backoff
- Batch auto-selection picks the result with the highest `vote_count` to avoid obscure partial-name matches

---

## File Structure

```
TMDB-Backdrop-Downloader/
  main.py              Main script
  requirements.txt     Python dependencies
  .env.example         Example environment file
```

---

---

## Install as a command (pipx)

Install this folder as a CLI so it is available on your PATH:

```bash
pipx install .
tmdb-backdrop-downloader
```

Logging: set `LOG_LEVEL` (e.g. `DEBUG`) and `LOG_FILE` to also write logs to a file.


## Get the Code

Clone with git:

```bash
git clone https://github.com/drew-codes-things/TmdbBackdropDL.git
```

Or with the [GitHub CLI](https://cli.github.com/):

```bash
gh repo clone drew-codes-things/TmdbBackdropDL
```

## License

MIT - made by [Drew](https://github.com/drew-codes-things)
