# Local wheelhouse

Third-party wheel files are intentionally not committed. Build them for the
target platform with:

```bash
bash scripts/build_wheelhouse.sh
```

Then build the container without PyPI access:

```bash
PIP_NO_INDEX=1 docker compose build attendance_bot
```
