# Workspace Index

Per-developer journals live under `.trellis/workspace/<name>/`.

## Active Developers

| Developer | Journal index |
|-----------|---------------|
| DouyinCut | [Sessions and active journal](DouyinCut/index.md) |

See the developer index for the current session count and active journal.

## Getting Started

```bash
python ./.trellis/scripts/init_developer.py <your-name>
```

Record a session:

```bash
python ./.trellis/scripts/add_session.py --title "Short title" --commit - --summary "..."
```

Journals rotate at 2000 lines (`journal-N.md`).
