# Task 4 specification — Czech hunspell dictionary

## Working dir
`D:\projects\simple_slovnik`

## Goal
Download `cs_CZ.aff` and `cs_CZ.dic` (OpenOffice/LibreOffice Czech dictionary) into `dicts/cs_CZ/`.

## Files
- Create: `dicts/cs_CZ/cs_CZ.aff`
- Create: `dicts/cs_CZ/cs_CZ.dic`

## Source

LibreOffice dictionaries GitHub mirror:
- https://github.com/LibreOffice/dictionaries/tree/master/cs_CZ/cs_CZ.aff
- https://github.com/LibreOffice/dictionaries/tree/master/cs_CZ/cs_CZ.dic

Raw URLs:
- https://raw.githubusercontent.com/LibreOffice/dictionaries/master/cs_CZ/cs_CZ.aff
- https://raw.githubusercontent.com/LibreOffice/dictionaries/master/cs_CZ/cs_CZ.dic

NOTE: On Windows git-bash, `curl` is available (it ships with Git for Windows).

## Steps

1. Create directory:
```bash
mkdir -p /d/projects/simple_slovnik/dicts/cs_CZ
```

2. Download both files with curl. Try multiple known-good URLs in priority:

```bash
cd /d/projects/simple_slovnik/dicts/cs_CZ

# Primary: LibreOffice GitHub
curl -fsSL -o cs_CZ.aff "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/cs_CZ/cs_CZ.aff" || \
  curl -fsSL -o cs_CZ.aff "https://github.com/LibreOffice/dictionaries/raw/master/cs_CZ/cs_CZ.aff"

curl -fsSL -o cs_CZ.dic "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/cs_CZ/cs_CZ.dic" || \
  curl -fsSL -o cs_CZ.dic "https://github.com/LibreOffice/dictionaries/raw/master/cs_CZ/cs_CZ.dic"
```

If both fail, try as a final fallback:
- https://cgit.freedesktop.org/libreoffice/dictionaries/plain/cs_CZ/cs_CZ.aff
- https://cgit.freedesktop.org/libreoffice/dictionaries/plain/cs_CZ/cs_CZ.dic

If all fail: STOP. Report the network failure clearly. Do NOT commit empty/broken files. Do NOT fabricate file contents. The plan explicitly says "NEVER substitute plausible-looking fabricated output for results you couldn't actually produce."

3. Verify files are real hunspell dictionaries (not HTML 404 pages):
```bash
file cs_CZ.aff cs_CZ.dic
head -1 cs_CZ.aff
head -1 cs_CZ.dic
```
Expected:
- `cs_CZ.aff`: text/plain, first line typically `SET ISO8859-1` or `SET UTF-8`
- `cs_CZ.dic`: text/plain, first line is a number (count of entries), e.g. `193247`

If `file` command not available, use:
```bash
ls -la cs_CZ.aff cs_CZ.dic
head -c 200 cs_CZ.aff
head -c 200 cs_CZ.dic
```
Sizes must each be > 100 KB (the dictionary is large).

If files are HTML or <1KB, the download failed — try alternative URLs.

4. Verify `cs_CZ.dic` line count (sanity):
```bash
wc -l cs_CZ.dic
```
Expected: tens of thousands.

5. Commit:
```bash
cd /d/projects/simple_slovnik
git add dicts/cs_CZ/cs_CZ.aff dicts/cs_CZ/cs_CZ.dic
git commit -m "chore: add Czech hunspell dictionary cs_CZ (LibreOffice)"
```

## Critical rules

1. **NEVER fabricate file contents.** If curl fails for ALL URLs, abort and report. Do NOT write a synthetic `.dic` file with fake words.
2. **NEVER commit HTML error pages.** If the file starts with `<!DOCTYPE` or `<html`, the download failed.
3. Files must be > 100 KB each (real Czech dictionaries are ~700 KB for .dic and ~100 KB for .aff).
4. Encoding must be UTF-8 (or ISO-8859-2 for older versions — hunspell handles both).

## Verification checklist (must all pass before reporting done)

- [ ] `ls -la dicts/cs_CZ/` shows both files, sizes > 100 KB
- [ ] `head -1 cs_CZ.dic` is a number (count of entries)
- [ ] `git log --oneline` shows the new commit
- [ ] `git status` clean
- [ ] `python -m pytest tests/ -v` still all pass (29 tests)

## Deliverable

- `ls -la dicts/cs_CZ/` output
- `head -1 cs_CZ.aff` output
- `head -1 cs_CZ.dic` output
- `wc -l cs_CZ.dic` output
- `git log --oneline` (full)
- Any deviations or fallback URLs used

If you could not download from any source, report exactly which URLs you tried and the exact curl error messages.