---
title: Plugins: your own hive commands
keywords: plugin, extension, extensions, hive-name, custom command, own command, extend
see: api, hooks
---
# Plugins

A program called `hive-NAME` on your PATH becomes the command `hive NAME`, as with git,
kubectl and cargo:

```sh
hive standup --since yesterday      # runs hive-standup --since yesterday
```

- It gets the arguments after its name.
- Its environment has `HIVE_HOME` (Hive's folder, normally `~/.hive`) and, when you passed
  `--project`, `HIVE_PROJECT`.
- It can call `hive api …` for anything on the server, with your admin login.
- On Windows, `hive-NAME.exe`, `.cmd`, `.bat` and the other extensions in `PATHEXT` count.

Hive's own commands always win over a plugin of the same name. Plugins don't run inside an
agent's session, so an agent can't use one to act as you.
