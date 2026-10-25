---
title: Settings presets: solo, team, strict
keywords: preset, fleet.preset, solo, team, strict, custom, starting point
see: config, review, permissions
---
# Presets

A preset is a ready-made bundle of settings to start from. Choose one in `hive.toml`:

```sh
hive config set fleet.preset team
```

- `custom` (the default): nothing extra.
- `solo`: no reviews: work reaches `main` as soon as its checks pass.
- `team`: every piece of work is reviewed by an agent with the `reviewer` role (or by the
  orchestrator when no other reviewer exists) before it reaches `main`.
- `strict`: reviews as in `team`, and the `strict` permission preset, which leaves out
  building and running tools, packages, web reads and docker from what agents may run
  without approval.

A preset sits below everything you set yourself: any setting in `hive.toml`, your user
settings or `hive.local.toml` overrides it. `hive config explain integration.review` shows
whether a value comes from the preset.

The server gets `hive.toml` with its preset applied, so every computer agrees on it.
